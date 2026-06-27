"""
Modular StockRadar analysis pipeline.

This is the API-facing orchestration layer for the newer architecture:

    collectors -> features -> scoring -> risk -> lake/API

It returns the same result shape that the React dashboard already consumes.
"""

from __future__ import annotations

import concurrent.futures
import logging
import math
from datetime import datetime
from typing import Any

import pandas as pd
import yfinance as yf

from core.collectors.nse import _make_session, collect_all_quotes
from core.features.regime import compute_regime_features
from core.features.store import compute_all_features
from core.lake.manager import close_lake, get_lake
from core.lake.schema import init_schema
from core.risk.engine import apply_risk_to_results
from core.scoring.hybrid import calculate_final_score

log = logging.getLogger("stockradar.pipeline")

MAX_WORKERS = 8
MIN_HISTORY_ROWS = 60
FALLBACK_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "SBIN", "BHARTIARTL", "ITC", "LT", "AXISBANK",
    "KOTAKBANK", "HCLTECH", "SUNPHARMA", "TATAMOTORS", "WIPRO",
]


def _clean_number(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def _download_history(symbol: str) -> pd.DataFrame | None:
    try:
        df = yf.download(symbol, period="1y", interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < MIN_HISTORY_ROWS:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
        return df
    except Exception as exc:
        log.warning("History download failed for %s: %s", symbol, exc)
        return None


def _store_ohlcv(symbol: str, df: pd.DataFrame) -> None:
    try:
        lake = get_lake()
        out = df.reset_index().copy()
        date_col = "Date" if "Date" in out.columns else out.columns[0]
        out = out.rename(
            columns={
                date_col: "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        out["symbol"] = symbol.replace(".NS", "")
        out["source"] = "yfinance"
        out = out[["symbol", "date", "open", "high", "low", "close", "volume", "source"]]
        lake.register("ohlcv_batch", out)
        lake.execute(
            """
            INSERT OR REPLACE INTO raw_ohlcv
            (symbol, date, open, high, low, close, volume, source)
            SELECT symbol, date, open, high, low, close, volume, source
            FROM ohlcv_batch
            """
        )
        lake.unregister("ohlcv_batch")
    except Exception as exc:
        log.debug("OHLCV lake write failed for %s: %s", symbol, exc)


def _market_payload(regime: dict | None) -> dict:
    regime = regime or {}
    label = regime.get("regime_label") or regime.get("market_regime") or "NEUTRAL"
    return {
        "market_trend": str(label).upper(),
        "vix_value": _clean_number(regime.get("india_vix")),
        "nifty_50_change": _clean_number(regime.get("nifty_change_1d"), 0),
        "sector_changes": regime.get("sector_breadth") or {},
        "regime": regime,
    }


def _regime_code(regime: dict | None) -> int:
    label = str((regime or {}).get("regime_label") or (regime or {}).get("market_regime") or "").upper()
    if "BULL" in label:
        return 2
    if "BEAR" in label:
        return 0
    return 1


def _score_inputs(features: dict, quote: dict, market: dict) -> tuple[dict, dict, dict, dict, dict, dict]:
    technical = {
        "rsi": features.get("t_rsi_14"),
        "macd_hist": features.get("t_macd_histogram"),
        "adx": features.get("t_adx"),
        "bb_pct": features.get("t_bb_pct"),
        "stoch_k": features.get("t_stoch_k"),
        "roc_5d": features.get("t_roc_5d"),
        "sma_ratio_50_200": features.get("t_sma_ratio"),
        "volume_ratio": features.get("t_volume_ratio"),
        "rs_vs_nifty": features.get("t_rs_vs_nifty"),
        "is_52w_breakout": features.get("t_breakout_52w"),
        "hh_hl_count": features.get("t_hh_hl_count"),
        "atr_pct": features.get("t_atr_pct"),
    }
    fundamental = {
        "roe": features.get("f_roe"),
        "roce": features.get("f_roce"),
        "eps_growth_1y": features.get("f_eps_growth_1y"),
        "revenue_growth_1y": features.get("f_revenue_growth_1y"),
        "debt_to_equity": features.get("f_debt_to_equity"),
        "fcf_yield": features.get("f_fcf_yield"),
        "pe_ratio": features.get("f_pe_ratio"),
        "promoter_holding": features.get("f_promoter_holding"),
        "pledged_pct": features.get("f_pledged_pct"),
        "market_cap_cr": features.get("f_market_cap_cr"),
    }
    institutional = {
        "fii_3d_net": features.get("i_fii_net_3d"),
        "dii_3d_net": features.get("i_dii_net_3d"),
        "delivery_pct_5d": features.get("i_delivery_5d_avg") or features.get("i_delivery_pct"),
        "delivery_spike": features.get("i_delivery_spike"),
        "pcr": features.get("i_pcr"),
        "oi_buildup": features.get("i_oi_buildup_bullish"),
        "max_pain_dist": features.get("i_max_pain_distance_pct"),
    }
    sentiment = {
        "avg_score": features.get("s_vader_score") or features.get("s_sentiment_score"),
        "article_count": features.get("s_headline_count"),
        "event_type": features.get("s_event_type"),
        "gdelt_tone": features.get("s_gdelt_tone"),
    }
    stock_meta = {
        "symbol": features.get("symbol"),
        "industry": quote.get("industry") or features.get("f_industry") or "",
        "market_cap_cr": features.get("f_market_cap_cr"),
        "avg_volume": quote.get("totalTradedVolume"),
        "pledged_pct": features.get("f_pledged_pct"),
        "atr_pct": features.get("t_atr_pct"),
        "event_type": features.get("s_event_type"),
    }
    market_for_score = {
        **market,
        "sector_changes": market.get("sector_changes") or {},
        "sector_momentum": features.get("sector_score"),
        "breadth_pct": features.get("r_breadth_pct"),
        "crude_regime": features.get("r_crude_regime"),
        "usdinr_regime": features.get("r_usdinr_regime"),
    }
    return fundamental, technical, institutional, sentiment, market_for_score, stock_meta


def _signal(score: float) -> str:
    if score >= 75:
        return "BUY"
    if score >= 60:
        return "WATCH"
    if score >= 40:
        return "HOLD"
    return "SELL"


def _result_from_features(symbol: str, features: dict, quote: dict, score_result: dict) -> dict:
    score = _clean_number(score_result.get("final_score"), features.get("composite_score") or 50.0) or 50.0
    price = _clean_number(quote.get("lastPrice"), features.get("t_price") or 0.0)
    high_52w = _clean_number(quote.get("yearHigh"), features.get("t_high_52w") or price)
    low_52w = _clean_number(quote.get("yearLow"), features.get("t_low_52w") or price)
    news_label = features.get("s_sentiment_label") or features.get("s_label") or "NEUTRAL"
    news_score = _clean_number(features.get("s_sentiment_score"), 0) or 0

    result = {
        "symbol": symbol.replace(".NS", ""),
        "price": round(price or 0, 2),
        "score": round(score, 1),
        "signal": score_result.get("signal") or _signal(score),
        "rsi": round(_clean_number(features.get("t_rsi_14"), 50) or 50, 1),
        "adx": round(_clean_number(features.get("t_adx"), 20) or 20, 1),
        "macd_bullish": bool(features.get("t_macd_bullish")),
        "golden_cross": bool(features.get("t_golden_cross")),
        "vol_surge": bool(features.get("t_volume_surge")),
        "volume_ratio": _clean_number(features.get("t_volume_ratio"), 1) or 1,
        "stoch_k": round(_clean_number(features.get("t_stoch_k"), 50) or 50, 1),
        "bb_pct": round(_clean_number(features.get("t_bb_pct"), 0.5) or 0.5, 2),
        "atr_pct": _clean_number(features.get("t_atr_pct"), 0) or 0,
        "roc_5d": _clean_number(features.get("t_roc_5d"), 0) or 0,
        "roc_10d": _clean_number(features.get("t_roc_10d"), 0) or 0,
        "high_52w": round(high_52w or price or 0, 2),
        "low_52w": round(low_52w or price or 0, 2),
        "pct_from_52w_high": _clean_number(features.get("t_pct_from_52w_high"), 0) or 0,
        "pct_from_52w_low": _clean_number(features.get("t_pct_from_52w_low"), 0) or 0,
        "sma_200": round(_clean_number(features.get("t_sma_200"), 0) or 0, 2),
        "sma_50": round(_clean_number(features.get("t_sma_50"), 0) or 0, 2),
        "promoter_action": "NEUTRAL",
        "promoter_detail": "Handled by modular fundamental/institutional features",
        "intraday_change": _clean_number(quote.get("pChange"), 0) or 0,
        "open_gap": 0,
        "news_sentiment": str(news_label).upper(),
        "news_score": news_score,
        "top_news": features.get("s_top_news") or features.get("s_headlines") or [],
        "pe_ratio": features.get("f_pe_ratio"),
        "revenue_growth": features.get("f_revenue_growth_1y"),
        "eps_growth": features.get("f_eps_growth_1y"),
        "analyst_rating": features.get("f_analyst_rating"),
        "company_name": quote.get("companyName") or features.get("f_company_name"),
        "industry": quote.get("industry") or features.get("f_industry") or "Unknown",
        "live_volume": quote.get("totalTradedVolume"),
        "delivery_pct": features.get("i_delivery_pct"),
        "delivery_pct_5d": features.get("i_delivery_5d_avg"),
        "pledged_pct": features.get("f_pledged_pct"),
        "market_cap_cr": features.get("f_market_cap_cr"),
        "factor_scores": score_result.get("factor_scores"),
        "top_reasons": score_result.get("top_reasons", []),
        "regime_multiplier": score_result.get("regime_multiplier") or features.get("regime_multiplier"),
        "pipeline": "core_modular",
    }

    if result["high_52w"] and result["price"]:
        result["pct_from_52w_high"] = round(((result["high_52w"] - result["price"]) / result["high_52w"]) * 100, 2)
    if result["low_52w"] and result["price"]:
        result["pct_from_52w_low"] = round(((result["price"] - result["low_52w"]) / result["low_52w"]) * 100, 2)

    return result


def analyse_symbol(symbol: str, quote: dict, nifty_df: pd.DataFrame | None, regime: dict | None, market: dict, session: object) -> dict | None:
    try:
        yf_symbol = symbol if symbol.endswith(".NS") else f"{symbol}.NS"
        df = _download_history(yf_symbol)
        if df is None:
            return None

        _store_ohlcv(symbol, df)
        ticker = yf.Ticker(yf_symbol)
        features = compute_all_features(
            yf_symbol,
            df,
            nifty_df=nifty_df,
            regime=regime,
            ticker=ticker,
            nse_session=session,
            company_name=quote.get("companyName") or symbol,
        )
        fundamental, technical, institutional, sentiment, score_market, stock_meta = _score_inputs(features, quote, market)
        score_result = calculate_final_score(
            fundamental=fundamental,
            technical=technical,
            institutional=institutional,
            sentiment=sentiment,
            market=score_market,
            stock_meta=stock_meta,
            regime=_regime_code(regime),
        )
        return _result_from_features(yf_symbol, features, quote, score_result)
    finally:
        close_lake()


def run_modular_analysis(limit: int | None = None) -> list[dict]:
    """
    Run the full modular analysis pipeline and return API-ready stock results.
    """
    try:
        log.info("Modular analysis started at %s", datetime.now().isoformat())
        init_schema()

        session = _make_session()
        quotes = collect_all_quotes(session)
        if not quotes:
            log.warning("No NSE quotes collected; falling back to default modular watchlist")
            quotes = {sym: {"symbol": sym, "companyName": sym, "industry": ""} for sym in FALLBACK_SYMBOLS}

        symbols = sorted(quotes.keys())
        if limit:
            symbols = symbols[:limit]

        regime = compute_regime_features()
        market = _market_payload(regime)
        nifty_df = _download_history("^NSEI")

        results: list[dict] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(analyse_symbol, sym, quotes.get(sym, {}), nifty_df, regime, market, session): sym
                for sym in symbols
            }
            for future in concurrent.futures.as_completed(futures):
                sym = futures[future]
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                except Exception as exc:
                    log.exception("Modular analysis failed for %s: %s", sym, exc)

        results = apply_risk_to_results(results)
        results.sort(key=lambda item: item.get("score", 0), reverse=True)
        log.info("Modular analysis complete: %d/%d stocks processed", len(results), len(symbols))
        return results
    finally:
        close_lake()


def get_modular_market_conditions() -> dict:
    """Return market conditions using the modular regime feature layer."""
    try:
        init_schema()
        return _market_payload(compute_regime_features())
    finally:
        close_lake()
