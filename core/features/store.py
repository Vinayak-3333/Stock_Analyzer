"""
Feature Store — Central Orchestrator
======================================
Computes ALL features for a given stock symbol by calling each feature module.
Returns a flat dict with all features + sub-scores.

Usage:
    features = compute_all_features("RELIANCE.NS", df, nifty_df, market_regime)
"""

import logging
from typing import Optional
import pandas as pd

from core.scoring.hybrid import WEIGHTS

log = logging.getLogger("stockradar.features.store")


def compute_all_features(
    symbol: str,
    df: pd.DataFrame,
    nifty_df: pd.DataFrame = None,
    regime: dict = None,
    ticker: object = None,
    nse_session: object = None,
    company_name: str = None,
) -> dict:
    """
    Master feature computation — calls all 4 feature modules and returns a
    single flat dict with every feature + individual sub-scores.

    Parameters
    ----------
    symbol       : Yahoo Finance symbol (e.g., 'RELIANCE.NS')
    df           : 1-year daily OHLCV DataFrame
    nifty_df     : Optional NIFTY 50 DataFrame for relative strength
    regime       : Pre-computed regime features dict (from compute_regime_features)
    ticker       : Optional pre-created yf.Ticker (avoids duplicate HTTP)
    nse_session  : Optional NSE requests.Session (for institutional data)
    company_name : Company name for news search

    Returns
    -------
    dict with keys: all feature values + sub-scores
        technical_score, fundamental_score, institutional_score,
        sentiment_score, regime_multiplier, composite_score
    """
    result = {
        "symbol": symbol.replace(".NS", ""),
        "technical_score": 50.0,
        "fundamental_score": 50.0,
        "institutional_score": 50.0,
        "sentiment_score": 50.0,
        "regime_multiplier": 1.0,
    }

    # ── 1. Technical Features ─────────────────────────────────────────────────
    try:
        from core.features.technical import compute_technical_features, compute_technical_score
        tech = compute_technical_features(df, nifty_df)
        result.update({f"t_{k}": v for k, v in tech.items()})
        result["technical_score"] = compute_technical_score(tech)
    except Exception as e:
        log.warning("Technical features failed for %s: %s", symbol, e)

    # ── 2. Fundamental Features ───────────────────────────────────────────────
    try:
        from core.features.fundamental import compute_fundamental_features, compute_fundamental_score
        fund = compute_fundamental_features(symbol, ticker=ticker)
        result.update({f"f_{k}": v for k, v in fund.items()})
        result["fundamental_score"] = compute_fundamental_score(fund)
    except Exception as e:
        log.warning("Fundamental features failed for %s: %s", symbol, e)

    # ── 3. Institutional Features ─────────────────────────────────────────────
    try:
        from core.features.institutional import compute_institutional_features, compute_institutional_score
        inst = compute_institutional_features(symbol, nse_session=nse_session)
        result.update({f"i_{k}": v for k, v in inst.items()})
        result["institutional_score"] = compute_institutional_score(inst)
    except Exception as e:
        log.warning("Institutional features failed for %s: %s", symbol, e)

    # ── 4. Sentiment Features ─────────────────────────────────────────────────
    try:
        from core.features.sentiment import compute_sentiment_features, compute_sentiment_score
        nse_code = symbol.replace(".NS", "")
        sent = compute_sentiment_features(symbol, company_name or nse_code, ticker=ticker)
        result.update({f"s_{k}": v for k, v in sent.items()})
        result["sentiment_score"] = compute_sentiment_score(sent)
    except Exception as e:
        log.warning("Sentiment features failed for %s: %s", symbol, e)

    # ── 5. Regime Multiplier ──────────────────────────────────────────────────
    if regime:
        try:
            from core.features.regime import get_regime_multiplier
            result["regime_multiplier"] = get_regime_multiplier(regime)
            # Copy regime features with prefix
            for k, v in regime.items():
                if k not in ("sector_breadth",):  # skip large dicts
                    result[f"r_{k}"] = v
        except Exception as e:
            log.warning("Regime multiplier failed: %s", e)

    # ── Composite Score (weights shared with core.scoring.hybrid) ─────────────
    # Sector score derived from regime sector data
    sector_score = 50.0
    if regime and regime.get("sector_breadth"):
        sector_values = [
            float(v)
            for v in regime["sector_breadth"].values()
            if v is not None and pd.notna(v)
        ]
        if sector_values:
            avg_sector = sum(sector_values) / len(sector_values)
            sector_score = max(0, min(100, 50 + avg_sector * 5))

    # Risk score derived from fundamental filters
    risk_score = 50.0
    pledged = result.get("f_pledged_pct", 0) or 0
    de = result.get("f_debt_to_equity") or 0
    mcap = result.get("f_market_cap_cr") or 10000
    atr_pct = result.get("t_atr_pct") or 2

    if pledged > 30:
        risk_score -= 20
    if de > 150:
        risk_score -= 10
    elif de < 30:
        risk_score += 5
    if mcap < 500:
        risk_score -= 15  # small cap penalty
    if atr_pct > 4:
        risk_score -= 5
    elif atr_pct < 1.5:
        risk_score += 5
    risk_score = max(0, min(100, risk_score))

    result["sector_score"] = round(sector_score, 1)
    result["risk_score"] = round(risk_score, 1)

    composite = (
        result["fundamental_score"]    * WEIGHTS["fundamental"]   +
        result["technical_score"]      * WEIGHTS["technical"]     +
        result["institutional_score"]  * WEIGHTS["institutional"] +
        result["sentiment_score"]      * WEIGHTS["sentiment"]     +
        sector_score                   * WEIGHTS["sector"]        +
        risk_score                     * WEIGHTS["risk"]
    )

    # Apply regime multiplier
    composite *= result["regime_multiplier"]
    composite = max(0, min(100, composite))

    result["composite_score"] = round(composite, 1)

    return result
