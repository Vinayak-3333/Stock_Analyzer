"""
Backtesting Engine (VectorBT-based)
=====================================
Vectorized backtesting using VectorBT.
Validates strategy quality before using it for live recommendations.

Metrics: CAGR, Sharpe, Sortino, Max Drawdown, Win Rate, Alpha vs NIFTY,
         Profit Factor, Avg Hold Days.
"""

import logging
import uuid
import json
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import numpy as np

log = logging.getLogger("stockradar.backtest")


def _safe_float(v) -> Optional[float]:
    try:
        f = float(v)
        return None if (np.isnan(f) or np.isinf(f)) else round(f, 4)
    except Exception:
        return None


# ── Core backtest engine ──────────────────────────────────────────────────────

def run_backtest(
    price_data: pd.DataFrame,       # MultiIndex (date, symbol) or wide (date x symbol)
    signals: pd.DataFrame,          # Same shape as price_data, 1=BUY 0=NEUTRAL -1=SELL
    hold_days: int = 10,
    initial_capital: float = 1_000_000,
    commission_pct: float = 0.001,  # 0.1% per trade (NSE brokerage + STT)
    slippage_pct: float = 0.001,    # 0.1% slippage
    nifty_returns: Optional[pd.Series] = None,
    strategy_name: str = "MultiFactorV2",
) -> dict:
    """
    Runs a vectorized backtest over price_data using signals.
    Uses equal-weight portfolio with max 20 positions open at once.

    Returns dict with all performance metrics.
    """
    try:
        import vectorbt as vbt
    except ImportError:
        log.warning("vectorbt not installed — falling back to manual backtest engine")
        return _manual_backtest(
            price_data, signals, hold_days, initial_capital,
            commission_pct, slippage_pct, nifty_returns, strategy_name
        )

    # ── Prepare data ──────────────────────────────────────────────────────────
    # Ensure price_data is wide format: dates as index, symbols as columns
    if isinstance(price_data.index, pd.MultiIndex):
        price_data = price_data.unstack(level=1)
        if isinstance(price_data.columns, pd.MultiIndex):
            price_data = price_data["close"]

    # Align signals
    signals = signals.reindex_like(price_data).fillna(0)

    entries = signals == 1
    exits   = signals == -1

    # Build vectorbt portfolio (equal-sized positions)
    size = initial_capital / 20   # spread across up to 20 stocks

    pf = vbt.Portfolio.from_signals(
        price_data,
        entries=entries,
        exits=exits,
        size=size,
        size_type="value",
        fees=commission_pct + slippage_pct,
        freq="1D",
        init_cash=initial_capital,
        group_by=False,
        cash_sharing=False,
    )

    # ── Extract metrics ───────────────────────────────────────────────────────
    total_return = pf.total_return().mean()
    n_days       = (price_data.index[-1] - price_data.index[0]).days
    cagr         = ((1 + total_return) ** (365 / max(n_days, 1)) - 1) * 100

    sharpe   = _safe_float(pf.sharpe_ratio().mean())
    sortino  = _safe_float(pf.sortino_ratio().mean())
    max_dd   = _safe_float(abs(pf.max_drawdown().mean()) * 100)
    win_rate = _safe_float(pf.win_rate().mean() * 100)
    n_trades = int(pf.stats()["Total Trades"].sum())

    # Alpha vs NIFTY
    alpha = None
    if nifty_returns is not None:
        nifty_cagr = _calc_cagr_from_returns(nifty_returns, n_days)
        alpha = round(cagr - nifty_cagr, 2)

    # Profit factor = gross profit / gross loss
    try:
        pf_stats = pf.stats()
        gross_profit = pf_stats.get("Gross Profit [%]", 0).mean()
        gross_loss   = abs(pf_stats.get("Gross Loss [%]", 1).mean())
        profit_factor = round(gross_profit / max(gross_loss, 0.001), 2)
    except Exception:
        profit_factor = None

    result = {
        "cagr":            round(cagr, 2),
        "sharpe":          sharpe,
        "sortino":         sortino,
        "max_drawdown":    max_dd,
        "win_rate":        win_rate,
        "total_trades":    n_trades,
        "alpha_vs_nifty":  alpha,
        "profit_factor":   profit_factor,
        "strategy_name":   strategy_name,
        "period_days":     n_days,
        "initial_capital": initial_capital,
    }
    log.info(
        "Backtest '%s': CAGR=%.1f%% Sharpe=%.2f MaxDD=%.1f%% WinRate=%.1f%%",
        strategy_name, cagr, sharpe or 0, max_dd or 0, win_rate or 0
    )
    return result


def _calc_cagr_from_returns(returns: pd.Series, n_days: int) -> float:
    cumulative = (1 + returns).prod()
    return ((cumulative ** (365 / max(n_days, 1))) - 1) * 100


# ── Manual backtest (fallback when vectorbt not installed) ────────────────────

def _manual_backtest(
    price_data: pd.DataFrame,
    signals: pd.DataFrame,
    hold_days: int,
    initial_capital: float,
    commission_pct: float,
    slippage_pct: float,
    nifty_returns: Optional[pd.Series],
    strategy_name: str,
) -> dict:
    """
    Simple vectorized backtest without vectorbt.
    For each BUY signal: enter at next open, exit after hold_days or SELL signal.
    """
    trades = []
    total_cost = commission_pct + slippage_pct

    symbols = price_data.columns.tolist()
    dates   = price_data.index.tolist()

    for sym in symbols:
        prices  = price_data[sym].dropna()
        sigs    = signals[sym] if sym in signals.columns else pd.Series(0, index=prices.index)
        in_trade = False
        entry_price = 0
        entry_date  = None

        for i, dt in enumerate(prices.index):
            sig = sigs.get(dt, 0)

            if not in_trade and sig == 1:
                # Enter at next day open (approximated as close * 1.002)
                entry_price = prices.iloc[i] * (1 + slippage_pct)
                entry_date  = dt
                in_trade = True

            elif in_trade:
                days_held = (dt - entry_date).days
                should_exit = (sig == -1) or (days_held >= hold_days)

                if should_exit:
                    exit_price = prices.iloc[i] * (1 - slippage_pct)
                    net_return = (exit_price - entry_price) / entry_price
                    net_return -= 2 * commission_pct   # buy + sell commission
                    trades.append({
                        "symbol":     sym,
                        "entry_date": entry_date,
                        "exit_date":  dt,
                        "entry":      entry_price,
                        "exit":       exit_price,
                        "return_pct": net_return * 100,
                        "days_held":  days_held,
                    })
                    in_trade = False

    if not trades:
        return {"cagr": 0, "sharpe": 0, "sortino": 0, "max_drawdown": 0,
                "win_rate": 0, "total_trades": 0, "strategy_name": strategy_name}

    trades_df  = pd.DataFrame(trades)
    returns    = trades_df["return_pct"].values / 100

    # Portfolio equity curve (equal-weight, sequential approximation)
    equity = initial_capital
    equity_curve = [initial_capital]
    for ret in returns:
        allocation = equity / 20   # assume 1/20 portfolio per trade
        equity += allocation * ret
        equity_curve.append(equity)

    total_return  = (equity - initial_capital) / initial_capital * 100
    n_days        = (dates[-1] - dates[0]).days if len(dates) > 1 else 252
    cagr          = ((1 + total_return / 100) ** (365 / max(n_days, 1)) - 1) * 100

    eq_series  = pd.Series(equity_curve)
    daily_ret  = eq_series.pct_change().dropna()
    sharpe     = _safe_float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0
    downside   = daily_ret[daily_ret < 0]
    sortino    = _safe_float(daily_ret.mean() / downside.std() * np.sqrt(252)) if len(downside) > 0 else 0
    roll_max   = eq_series.cummax()
    drawdowns  = (eq_series - roll_max) / roll_max
    max_dd     = _safe_float(abs(drawdowns.min()) * 100)
    win_rate   = _safe_float((returns > 0).mean() * 100)
    gross_pos  = returns[returns > 0].sum()
    gross_neg  = abs(returns[returns < 0].sum())
    profit_factor = round(gross_pos / max(gross_neg, 0.001), 2)

    # Alpha vs NIFTY
    alpha = None
    if nifty_returns is not None:
        nifty_cagr = _calc_cagr_from_returns(nifty_returns, n_days)
        alpha = round(cagr - nifty_cagr, 2)

    result = {
        "cagr":            round(cagr, 2),
        "sharpe":          sharpe,
        "sortino":         sortino,
        "max_drawdown":    max_dd,
        "win_rate":        win_rate,
        "total_trades":    len(trades),
        "profit_factor":   profit_factor,
        "avg_hold_days":   round(trades_df["days_held"].mean(), 1),
        "alpha_vs_nifty":  alpha,
        "strategy_name":   strategy_name,
        "period_days":     n_days,
    }
    log.info(
        "Manual Backtest '%s': CAGR=%.1f%% Sharpe=%.2f MaxDD=%.1f%% WinRate=%.1f%% Trades=%d",
        strategy_name, cagr, sharpe or 0, max_dd or 0, win_rate or 0, len(trades)
    )
    return result


# ── Save backtest results to lake ─────────────────────────────────────────────

def save_backtest_to_lake(metrics: dict, config: dict = None):
    """Store backtest results in DuckDB for dashboard display."""
    from core.lake.manager import get_lake
    conn = get_lake()
    conn.execute("""
        INSERT INTO backtest_runs
            (id, strategy_name, lookback_days, hold_days, min_score,
             cagr, sharpe, sortino, max_drawdown, win_rate, alpha_vs_nifty,
             total_trades, profit_factor, avg_hold_days, config_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        str(uuid.uuid4()),
        metrics.get("strategy_name", "MultiFactorV2"),
        config.get("lookback_days", 252) if config else 252,
        config.get("hold_days", 10) if config else 10,
        config.get("min_score", 75) if config else 75,
        metrics.get("cagr"),
        metrics.get("sharpe"),
        metrics.get("sortino"),
        metrics.get("max_drawdown"),
        metrics.get("win_rate"),
        metrics.get("alpha_vs_nifty"),
        metrics.get("total_trades"),
        metrics.get("profit_factor"),
        metrics.get("avg_hold_days"),
        json.dumps(config or {}),
    ])
    conn.commit()
    log.info("Backtest results saved to lake")


def get_latest_backtest() -> dict:
    """Retrieve the most recent backtest results for dashboard display."""
    from core.lake.manager import get_lake
    conn = get_lake()
    try:
        row = conn.execute("""
            SELECT * FROM backtest_runs
            ORDER BY run_date DESC LIMIT 1
        """).fetchone()
        if row:
            cols = [d[0] for d in conn.description]
            return dict(zip(cols, row))
    except Exception:
        pass
    return {}
