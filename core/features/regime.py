"""
Market Regime Detection & Macro Features
==========================================

Detects the prevailing market regime (BULL / NEUTRAL / BEAR) using
rule-based logic on NIFTY 50 trend, India VIX, and breadth signals.
Also computes macro features: crude oil, USD/INR, US market trend,
US 10Y yield, and sector breadth.

All data is sourced from **yfinance** — no API keys required.

Usage
-----
>>> from core.features.regime import compute_regime_features, get_regime_multiplier
>>> features = compute_regime_features()
>>> multiplier = get_regime_multiplier(features)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level cache (key → (timestamp, payload))
# ---------------------------------------------------------------------------
_cache: Dict[str, tuple[float, Any]] = {}
_CACHE_TTL_SECONDS: int = 3600  # 1 hour


def _get_cached(key: str) -> Optional[Any]:
    """Return cached value if still fresh, else *None*."""
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, payload = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return payload


def _set_cache(key: str, value: Any) -> None:
    _cache[key] = (time.time(), value)


# ---------------------------------------------------------------------------
# yfinance helpers (lazy import to keep module import fast)
# ---------------------------------------------------------------------------

def _import_yfinance():
    """Lazily import yfinance and suppress its noisy loggers."""
    import yfinance as yf  # noqa: F811

    # Silence yfinance / peewee / urllib3 loggers
    for noisy in ("yfinance", "peewee", "urllib3", "urllib3.connectionpool"):
        logging.getLogger(noisy).setLevel(logging.CRITICAL)

    return yf


def _safe_download(
    ticker: str,
    period: str = "6mo",
    interval: str = "1d",
    *,
    progress: bool = False,
) -> Optional[pd.DataFrame]:
    """Download price history for *ticker*, returning *None* on any failure.

    Handles the single-column squeeze that yfinance sometimes returns.
    """
    try:
        yf = _import_yfinance()
        df = yf.download(
            ticker,
            period=period,
            interval=interval,
            progress=progress,
            auto_adjust=True,
        )
        if df is None or df.empty:
            logger.debug("No data returned for %s", ticker)
            return None

        # yfinance ≥0.2.31 may return MultiIndex columns for a single ticker
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel("Ticker")

        return df
    except Exception:
        logger.warning("Failed to download %s", ticker, exc_info=True)
        return None


def _latest_close(df: Optional[pd.DataFrame]) -> Optional[float]:
    """Extract the most-recent Close value from a DataFrame."""
    if df is None or df.empty:
        return None
    try:
        val = df["Close"].iloc[-1]
        return float(val) if pd.notna(val) else None
    except Exception:
        return None


def _pct_change_n(df: Optional[pd.DataFrame], n: int) -> Optional[float]:
    """Return the *n*-day percentage change of Close, or *None*."""
    if df is None or df.empty or len(df) < n + 1:
        return None
    try:
        cur = float(df["Close"].iloc[-1])
        prev = float(df["Close"].iloc[-(n + 1)])
        if prev == 0 or np.isnan(prev) or np.isnan(cur):
            return None
        return round((cur - prev) / prev * 100, 2)
    except Exception:
        return None


def _is_above_sma(df: Optional[pd.DataFrame], window: int = 200) -> Optional[bool]:
    """Check if the latest Close is above its *window*-period SMA."""
    if df is None or df.empty or len(df) < window:
        return None
    try:
        sma = float(df["Close"].rolling(window).mean().iloc[-1])
        close = float(df["Close"].iloc[-1])
        if np.isnan(sma) or np.isnan(close):
            return None
        return close > sma
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Sector tickers
# ---------------------------------------------------------------------------
_SECTOR_MAP: Dict[str, str] = {
    "IT": "^CNXIT",
    "Pharma": "^CNXPHARMA",
    "Banking": "^NSEBANK",
    "Auto": "^CNXAUTO",
    "FMCG": "^CNXFMCG",
    "Metal": "^CNXMETAL",
}


# ---------------------------------------------------------------------------
# Core public API
# ---------------------------------------------------------------------------

def compute_regime_features() -> Dict[str, Any]:
    """Compute market-regime and macro features.

    Returns a flat dictionary with the following keys:

    ============================  ========================================
    Key                           Description
    ============================  ========================================
    market_regime                 ``'BULL'`` | ``'NEUTRAL'`` | ``'BEAR'``
    regime_score                  ``2`` (bull) / ``1`` (neutral) / ``0`` (bear)
    nifty_5d_change               NIFTY 50 5-day % change
    nifty_20d_change              NIFTY 50 20-day % change
    nifty_above_200sma            *bool* — Close > 200-SMA
    vix_value                     India VIX last value
    vix_regime                    ``'LOW'`` | ``'MEDIUM'`` | ``'HIGH'``
    breadth_pct                   % of NIFTY 50 stocks > 200-SMA (estimate)
    crude_change_1m               Crude 1-month % change
    crude_regime                  ``'RISING'`` | ``'STABLE'`` | ``'FALLING'``
    usdinr_change_1m              USD/INR 1-month % change
    usdinr_trend                  ``'STRENGTHENING'`` | ``'WEAKENING'`` | ``'STABLE'``
    us_market_trend               ``'BULLISH'`` | ``'NEUTRAL'`` | ``'BEARISH'``
    us_10y_yield                  US 10-year bond yield (%)
    sector_breadth                *dict* sector → 1-day % change
    computed_at                   ISO timestamp of computation
    ============================  ========================================
    """
    # --- Check cache first ---
    cached = _get_cached("regime_features")
    if cached is not None:
        logger.debug("Returning cached regime features")
        return cached

    logger.info("Computing regime features …")

    # --- Download all tickers in parallel-safe, individual calls ----------
    nifty_df = _safe_download("^NSEI", period="1y")
    vix_df = _safe_download("^INDIAVIX", period="3mo")
    crude_df = _safe_download("CL=F", period="3mo")
    usdinr_df = _safe_download("USDINR=X", period="3mo")
    sp500_df = _safe_download("^GSPC", period="3mo")
    tnx_df = _safe_download("^TNX", period="1mo")

    # --- NIFTY 50 ----------------------------------------------------------
    nifty_5d = _pct_change_n(nifty_df, 5)
    nifty_20d = _pct_change_n(nifty_df, 20)
    nifty_above_200 = _is_above_sma(nifty_df, 200)

    # --- India VIX ---------------------------------------------------------
    vix_value = _latest_close(vix_df)

    vix_regime: str
    if vix_value is None:
        vix_regime = "MEDIUM"  # conservative default
    elif vix_value < 15:
        vix_regime = "LOW"
    elif vix_value <= 20:
        vix_regime = "MEDIUM"
    else:
        vix_regime = "HIGH"

    # --- Breadth estimate (% of NIFTY 50 constituents > 200-SMA) -----------
    breadth_pct = _estimate_nifty50_breadth()

    # --- Crude oil ---------------------------------------------------------
    crude_1m = _pct_change_n(crude_df, 22)  # ~1 trading month

    crude_regime: str
    if crude_1m is None:
        crude_regime = "STABLE"
    elif crude_1m > 5:
        crude_regime = "RISING"
    elif crude_1m < -5:
        crude_regime = "FALLING"
    else:
        crude_regime = "STABLE"

    # --- USD / INR ---------------------------------------------------------
    usdinr_1m = _pct_change_n(usdinr_df, 22)

    usdinr_trend: str
    if usdinr_1m is None:
        usdinr_trend = "STABLE"
    elif usdinr_1m < -0.5:
        # INR gaining (USD/INR going down → INR strengthening)
        usdinr_trend = "STRENGTHENING"
    elif usdinr_1m > 0.5:
        usdinr_trend = "WEAKENING"
    else:
        usdinr_trend = "STABLE"

    # --- US market ---------------------------------------------------------
    sp500_5d = _pct_change_n(sp500_df, 5)

    us_trend: str
    if sp500_5d is None:
        us_trend = "NEUTRAL"
    elif sp500_5d > 1.0:
        us_trend = "BULLISH"
    elif sp500_5d < -1.0:
        us_trend = "BEARISH"
    else:
        us_trend = "NEUTRAL"

    # --- US 10-year yield --------------------------------------------------
    us_10y = _latest_close(tnx_df)

    # --- Sector breadth (1-day % change) -----------------------------------
    sector_breadth = _compute_sector_breadth()

    # --- Regime classification (rule-based) --------------------------------
    market_regime, regime_score = _classify_regime(
        nifty_5d=nifty_5d,
        nifty_20d=nifty_20d,
        nifty_above_200=nifty_above_200,
        vix=vix_value,
    )

    features: Dict[str, Any] = {
        "market_regime": market_regime,
        "regime_score": regime_score,
        "nifty_5d_change": nifty_5d,
        "nifty_20d_change": nifty_20d,
        "nifty_above_200sma": nifty_above_200,
        "vix_value": vix_value,
        "vix_regime": vix_regime,
        "breadth_pct": breadth_pct,
        "crude_change_1m": crude_1m,
        "crude_regime": crude_regime,
        "usdinr_change_1m": usdinr_1m,
        "usdinr_trend": usdinr_trend,
        "us_market_trend": us_trend,
        "us_10y_yield": us_10y,
        "sector_breadth": sector_breadth,
        "computed_at": datetime.now().isoformat(timespec="seconds"),
    }

    _set_cache("regime_features", features)
    logger.info(
        "Regime → %s (score=%d) | VIX=%.1f (%s)",
        market_regime,
        regime_score,
        vix_value if vix_value is not None else 0.0,
        vix_regime,
    )
    return features


def get_regime_multiplier(features: Dict[str, Any]) -> float:
    """Return a scoring multiplier based on the detected market regime.

    Parameters
    ----------
    features : dict
        Output of :func:`compute_regime_features`.

    Returns
    -------
    float
        ``1.10`` for BULL, ``1.00`` for NEUTRAL, ``0.85`` for BEAR.
    """
    regime = features.get("market_regime", "NEUTRAL")
    multipliers = {
        "BULL": 1.10,
        "NEUTRAL": 1.00,
        "BEAR": 0.85,
    }
    return multipliers.get(regime, 1.00)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _classify_regime(
    *,
    nifty_5d: Optional[float],
    nifty_20d: Optional[float],
    nifty_above_200: Optional[bool],
    vix: Optional[float],
) -> tuple[str, int]:
    """Rule-based regime classification.

    Rules
    -----
    * **BULL**: nifty_20d > 3 % AND nifty_above_200sma AND VIX < 18
    * **BEAR**: nifty_20d < -3 % OR (VIX > 22 AND nifty_5d < -1 %)
    * **NEUTRAL**: everything else

    Returns ``(regime_label, score)`` where score ∈ {0, 1, 2}.
    """
    # Safe defaults for missing data → lean toward NEUTRAL
    _nifty_5d = nifty_5d if nifty_5d is not None else 0.0
    _nifty_20d = nifty_20d if nifty_20d is not None else 0.0
    _above_200 = nifty_above_200 if nifty_above_200 is not None else True
    _vix = vix if vix is not None else 16.0  # middle ground

    # BULL check
    if _nifty_20d > 3.0 and _above_200 and _vix < 18.0:
        return ("BULL", 2)

    # BEAR check
    if _nifty_20d < -3.0:
        return ("BEAR", 0)
    if _vix > 22.0 and _nifty_5d < -1.0:
        return ("BEAR", 0)

    return ("NEUTRAL", 1)


def _estimate_nifty50_breadth() -> Optional[float]:
    """Estimate % of NIFTY 50 stocks trading above their 200-day SMA.

    Uses a representative sample of large-cap NSE tickers to keep
    download time reasonable (~10 tickers).  Returns a float 0–100
    or *None* if data is unavailable.
    """
    # Representative large-cap NIFTY 50 constituents (NSE suffix)
    sample_tickers = [
        "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
        "HINDUNILVR.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS",
        "LT.NS", "AXISBANK.NS", "BAJFINANCE.NS", "MARUTI.NS", "TITAN.NS",
        "SUNPHARMA.NS", "HCLTECH.NS", "WIPRO.NS", "NTPC.NS", "POWERGRID.NS",
    ]

    above_count = 0
    total_valid = 0

    for ticker in sample_tickers:
        try:
            df = _safe_download(ticker, period="1y", interval="1d")
            result = _is_above_sma(df, 200)
            if result is not None:
                total_valid += 1
                if result:
                    above_count += 1
        except Exception:
            continue

    if total_valid == 0:
        return None

    return round(above_count / total_valid * 100, 1)


def _compute_sector_breadth() -> Dict[str, Optional[float]]:
    """Compute 1-day % change for key sectoral indices.

    Returns a dict like ``{"IT": 0.45, "Banking": -0.12, …}``.
    Missing sectors get *None*.
    """
    result: Dict[str, Optional[float]] = {}
    for sector_name, ticker in _SECTOR_MAP.items():
        try:
            df = _safe_download(ticker, period="5d", interval="1d")
            change = _pct_change_n(df, 1)
            result[sector_name] = change
        except Exception:
            result[sector_name] = None
    return result


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)-28s  %(levelname)-7s  %(message)s",
    )

    print("=" * 60)
    print("  Market Regime Detection — Self Test")
    print("=" * 60)

    feats = compute_regime_features()
    mult = get_regime_multiplier(feats)

    # Pretty-print features
    for k, v in feats.items():
        if isinstance(v, dict):
            print(f"  {k}:")
            for sk, sv in v.items():
                print(f"    {sk:>10}: {sv}")
        else:
            print(f"  {k:>22}: {v}")

    print(f"\n  Regime multiplier: {mult:.2f}")
    print("=" * 60)
