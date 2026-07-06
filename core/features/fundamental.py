"""
Fundamental Analysis Features
==============================
Computes fundamental metrics for a single Indian stock using yfinance data.

Features extracted:
  pe_ratio, forward_pe, pe_vs_sector, roe, roce, eps_growth_1y,
  revenue_growth_1y, debt_to_equity, current_ratio, fcf_yield,
  promoter_holding, pledged_pct, market_cap_cr, dividend_yield,
  analyst_rating, company_name

Each function is callable standalone for a single symbol and returns
a plain dict that can be consumed by the scoring engine or serialised
to DuckDB / JSON.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import time
from typing import Any, Optional

log = logging.getLogger("stockradar.features.fundamental")

# ── Defaults returned when data is unavailable ─────────────────────────────────

_DEFAULTS: dict[str, Any] = {
    "pe_ratio":          None,
    "forward_pe":        None,
    "pe_vs_sector":      None,
    "roe":               None,
    "roce":              None,
    "eps_growth_1y":     None,
    "revenue_growth_1y": None,
    "debt_to_equity":    None,
    "current_ratio":     None,
    "fcf_yield":         None,
    "promoter_holding":  None,
    "pledged_pct":       0.0,
    "market_cap_cr":     None,
    "dividend_yield":    None,
    "analyst_rating":    None,
    "company_name":      "",
}

DEFAULT_SECTOR_PE = 20.0  # fallback sector median P/E


# ── Helpers ────────────────────────────────────────────────────────────────────

def _safe(val: Any, default: float = 0.0) -> float:
    """Return *val* if it is a finite number, else *default*."""
    if val is None:
        return default
    try:
        v = float(val)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return v


def _safe_or_none(val: Any) -> Optional[float]:
    """Return *val* as float if finite, else ``None``."""
    if val is None:
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _pct(val: Any) -> Optional[float]:
    """Convert a 0-1 ratio to percentage, or return None."""
    v = _safe_or_none(val)
    if v is None:
        return None
    # yfinance sometimes returns already-percentage values (>1 or <-1)
    # Heuristic: if |v| <= 1, treat as ratio; else already percentage
    if -1.0 <= v <= 1.0:
        return round(v * 100, 2)
    return round(v, 2)


def _rate_limit_sleep() -> None:
    """Sleep a small random duration to respect yfinance rate limits."""
    time.sleep(random.uniform(0.1, 0.4))


# ── Lake-backed cache ──────────────────────────────────────────────────────────
# Fundamentals don't change intraday, so each symbol's feature dict is cached
# in DuckDB and only re-fetched from yfinance after the TTL expires.

FUNDAMENTALS_CACHE_TTL_DAYS = int(os.getenv("FUNDAMENTALS_CACHE_TTL_DAYS", "7"))


def _load_cached_features(symbol: str) -> Optional[dict]:
    """Return the cached feature dict for *symbol* if fresh, else None."""
    try:
        from core.lake.manager import get_lake
        conn = get_lake()
        # Stagger expiry by 0-2 days per symbol so a cold start doesn't make
        # the whole shortlist expire (and re-fetch) on the same day.
        ttl = FUNDAMENTALS_CACHE_TTL_DAYS + (sum(symbol.encode()) % 3)
        row = conn.execute(
            "SELECT features_json FROM fundamentals_cache "
            "WHERE symbol = ? AND as_of >= current_date - ?",
            [symbol, ttl],
        ).fetchone()
        if row and row[0]:
            feats = json.loads(row[0])
            if isinstance(feats, dict):
                return feats
    except Exception as exc:
        log.debug("Fundamentals cache read failed for %s: %s", symbol, exc)
    return None


def _save_cached_features(symbol: str, features: dict) -> None:
    try:
        from core.lake.manager import get_lake
        conn = get_lake()
        conn.execute(
            "INSERT OR REPLACE INTO fundamentals_cache (symbol, as_of, features_json) "
            "VALUES (?, current_date, ?)",
            [symbol, json.dumps(features)],
        )
        conn.commit()
    except Exception as exc:
        log.debug("Fundamentals cache write failed for %s: %s", symbol, exc)


# ── Feature Extraction ────────────────────────────────────────────────────────

def compute_fundamental_features(
    symbol: str,
    ticker: object = None,
    use_cache: bool = True,
) -> dict:
    """
    Extract fundamental features for *symbol* from yfinance.

    Parameters
    ----------
    symbol : str
        Yahoo Finance symbol, e.g. ``'RELIANCE.NS'``.
    ticker : object, optional
        Pre-created ``yf.Ticker`` instance.  When supplied the function
        re-uses it to avoid creating a duplicate HTTP session.
    use_cache : bool
        Serve/refresh the DuckDB fundamentals cache
        (``FUNDAMENTALS_CACHE_TTL_DAYS``, default 7 days).  Pass ``False``
        to force a live yfinance fetch.

    Returns
    -------
    dict
        Standardised feature dict.  Keys match ``_DEFAULTS``.
        On total failure every value falls back to its default.
    """
    if use_cache:
        cached = _load_cached_features(symbol)
        if cached is not None:
            return cached

    features: dict[str, Any] = dict(_DEFAULTS)
    features["symbol"] = symbol

    # ── 1. Build / reuse Ticker ─────────────────────────────────────────────
    tk = ticker
    if tk is None:
        try:
            import yfinance as yf  # lazy import — heavy dependency
            _rate_limit_sleep()
            tk = yf.Ticker(symbol)
        except Exception as exc:
            log.error("Failed to create yfinance Ticker for %s: %s", symbol, exc)
            return features

    # ── 2. Fetch info dict ──────────────────────────────────────────────────
    info: dict = {}
    try:
        _rate_limit_sleep()
        info = tk.info or {}
    except Exception as exc:
        log.warning("Could not fetch .info for %s: %s", symbol, exc)

    # ── 3. Populate scalar features from info ───────────────────────────────
    features["company_name"] = info.get("longName") or info.get("shortName") or ""

    # P/E ratios
    features["pe_ratio"]  = _safe_or_none(info.get("trailingPE"))
    features["forward_pe"] = _safe_or_none(info.get("forwardPE"))

    # P/E relative to sector median
    pe = _safe_or_none(features["pe_ratio"])
    if pe is not None and pe > 0:
        features["pe_vs_sector"] = round(pe / DEFAULT_SECTOR_PE, 2)

    # Profitability
    features["roe"] = _pct(info.get("returnOnEquity"))

    # ROCE: yfinance does not expose ROCE directly.
    # Approximate as ROA × (1 + D/E) when not available.
    roce_raw = _safe_or_none(info.get("returnOnCapitalEmployed"))  # rarely present
    if roce_raw is not None:
        features["roce"] = _pct(roce_raw)
    else:
        roa = _safe_or_none(info.get("returnOnAssets"))
        de  = _safe_or_none(info.get("debtToEquity"))  # already %, e.g. 45.0
        if roa is not None and de is not None:
            leverage = 1.0 + de / 100.0
            # ROA is 0-1 ratio in yfinance
            roce_est = roa * leverage
            features["roce"] = _pct(roce_est)

    # Growth
    features["eps_growth_1y"]     = _pct(info.get("earningsGrowth"))
    features["revenue_growth_1y"] = _pct(info.get("revenueGrowth"))

    # Balance-sheet strength
    features["debt_to_equity"] = _safe_or_none(info.get("debtToEquity"))
    features["current_ratio"]  = _safe_or_none(info.get("currentRatio"))

    # FCF yield = Free Cash Flow / Market Cap × 100
    fcf = _safe_or_none(info.get("freeCashflow"))
    mcap = _safe_or_none(info.get("marketCap"))
    if fcf is not None and mcap and mcap > 0:
        features["fcf_yield"] = round((fcf / mcap) * 100, 2)

    # Market cap in crores (1 crore = 1e7)
    if mcap is not None and mcap > 0:
        features["market_cap_cr"] = round(mcap / 1e7, 2)

    # Dividend yield
    features["dividend_yield"] = _pct(info.get("dividendYield"))

    # Analyst consensus — Yahoo uses 1.0 (Strong Buy) to 5.0 (Strong Sell)
    features["analyst_rating"] = _safe_or_none(
        info.get("recommendationMean")
    )

    # ── 4. Promoter / insider holding ───────────────────────────────────────
    try:
        _rate_limit_sleep()
        holders = tk.major_holders
        if holders is not None and not holders.empty:
            # major_holders is a 2-column DataFrame; col 0 = value, col 1 = label
            for _, row in holders.iterrows():
                label = str(row.iloc[1]).lower() if len(row) > 1 else ""
                value = row.iloc[0]
                if "insider" in label or "promoter" in label:
                    parsed = _safe_or_none(value)
                    if parsed is not None:
                        # Could be 0-1 ratio or already %
                        features["promoter_holding"] = (
                            parsed * 100 if parsed <= 1 else parsed
                        )
                    break
    except Exception as exc:
        log.debug("Could not fetch major_holders for %s: %s", symbol, exc)

    # Pledged percentage is not available via yfinance — default 0
    features["pledged_pct"] = 0.0

    # ── 5. Fallback: try quarterly financials for growth if info was empty ──
    if features["eps_growth_1y"] is None:
        try:
            _rate_limit_sleep()
            qf = tk.quarterly_financials
            if qf is not None and not qf.empty:
                if "Net Income" in qf.index and qf.shape[1] >= 5:
                    recent_4q = qf.loc["Net Income"].iloc[:4].sum()
                    prior_4q  = qf.loc["Net Income"].iloc[4:8].sum()
                    if prior_4q and prior_4q != 0:
                        features["eps_growth_1y"] = round(
                            ((recent_4q - prior_4q) / abs(prior_4q)) * 100, 2
                        )
        except Exception as exc:
            log.debug("Quarterly financials fallback failed for %s: %s", symbol, exc)

    if features["revenue_growth_1y"] is None:
        try:
            _rate_limit_sleep()
            qf = tk.quarterly_financials
            if qf is not None and not qf.empty:
                label = None
                for candidate in ("Total Revenue", "Revenue", "Operating Revenue"):
                    if candidate in qf.index:
                        label = candidate
                        break
                if label and qf.shape[1] >= 5:
                    recent_4q = qf.loc[label].iloc[:4].sum()
                    prior_4q  = qf.loc[label].iloc[4:8].sum()
                    if prior_4q and prior_4q != 0:
                        features["revenue_growth_1y"] = round(
                            ((recent_4q - prior_4q) / abs(prior_4q)) * 100, 2
                        )
        except Exception as exc:
            log.debug("Revenue growth fallback failed for %s: %s", symbol, exc)

    # ── 6. Fallback: balance sheet for D/E and current ratio ────────────────
    if features["debt_to_equity"] is None or features["current_ratio"] is None:
        try:
            _rate_limit_sleep()
            bs = tk.balance_sheet
            if bs is not None and not bs.empty:
                if features["debt_to_equity"] is None:
                    total_debt = None
                    for col_name in ("Total Debt", "Long Term Debt", "Total Non Current Liabilities"):
                        if col_name in bs.index:
                            total_debt = _safe_or_none(bs.loc[col_name].iloc[0])
                            break
                    equity = None
                    for col_name in ("Stockholders Equity", "Total Stockholders Equity",
                                     "Stockholders' Equity", "Common Stock Equity"):
                        if col_name in bs.index:
                            equity = _safe_or_none(bs.loc[col_name].iloc[0])
                            break
                    if total_debt is not None and equity and equity != 0:
                        features["debt_to_equity"] = round(
                            (total_debt / equity) * 100, 2
                        )

                if features["current_ratio"] is None:
                    ca = None
                    cl = None
                    for col_name in ("Total Current Assets", "Current Assets"):
                        if col_name in bs.index:
                            ca = _safe_or_none(bs.loc[col_name].iloc[0])
                            break
                    for col_name in ("Total Current Liabilities", "Current Liabilities"):
                        if col_name in bs.index:
                            cl = _safe_or_none(bs.loc[col_name].iloc[0])
                            break
                    if ca is not None and cl and cl != 0:
                        features["current_ratio"] = round(ca / cl, 2)
        except Exception as exc:
            log.debug("Balance sheet fallback failed for %s: %s", symbol, exc)

    # Cache only when the fetch yielded real data — caching a throttled/empty
    # response would serve defaults for the whole TTL.
    if use_cache and (
        features.get("market_cap_cr") is not None
        or features.get("pe_ratio") is not None
        or features.get("roe") is not None
    ):
        _save_cached_features(symbol, features)

    log.info(
        "Fundamental features computed for %s — PE=%.1f  ROE=%s  D/E=%s",
        symbol,
        _safe(features["pe_ratio"]),
        features["roe"],
        features["debt_to_equity"],
    )
    return features


# ── Scoring ────────────────────────────────────────────────────────────────────

def compute_fundamental_score(features: dict) -> float:
    """
    Convert a fundamental feature dict into a 0–100 sub-score.

    Scoring rules are additive from a base of 50, then clamped to [0, 100].

    Parameters
    ----------
    features : dict
        Output of :func:`compute_fundamental_features`.

    Returns
    -------
    float
        Normalised fundamental score in ``[0.0, 100.0]``.
    """
    score = 50.0

    roe          = _safe(features.get("roe"))
    roce         = _safe(features.get("roce"))
    eps_growth   = _safe(features.get("eps_growth_1y"))
    rev_growth   = _safe(features.get("revenue_growth_1y"))
    de           = _safe(features.get("debt_to_equity"), 100)
    cr           = _safe(features.get("current_ratio"), 1.0)
    fcf_yield    = _safe(features.get("fcf_yield"))
    pe_vs_sector = _safe(features.get("pe_vs_sector"), 1.0)
    pledged      = _safe(features.get("pledged_pct"))
    analyst      = _safe(features.get("analyst_rating"), 3.0)
    div_yield    = _safe(features.get("dividend_yield"))

    # ── ROE ─────────────────────────────────────────────────────────────────
    if roe > 20:
        score += 10
    elif roe > 15:
        score += 5
    elif roe < 5:
        score -= 5

    # ── ROCE ────────────────────────────────────────────────────────────────
    if roce > 20:
        score += 8
    elif roce > 12:
        score += 4
    elif roce < 5:
        score -= 4

    # ── EPS growth ──────────────────────────────────────────────────────────
    if eps_growth > 20:
        score += 10
    elif eps_growth > 10:
        score += 5
    elif eps_growth < -10:
        score -= 8

    # ── Revenue growth ──────────────────────────────────────────────────────
    if rev_growth > 20:
        score += 8
    elif rev_growth > 10:
        score += 4
    elif rev_growth < -10:
        score -= 6

    # ── Debt / Equity ───────────────────────────────────────────────────────
    if de < 30:
        score += 6   # clean balance sheet
    elif de > 150:
        score -= 8   # high debt

    # ── Current Ratio ───────────────────────────────────────────────────────
    if cr > 1.5:
        score += 4
    elif cr < 1.0:
        score -= 4

    # ── FCF Yield ───────────────────────────────────────────────────────────
    if fcf_yield > 5:
        score += 5
    elif fcf_yield < 0:
        score -= 5

    # ── P/E vs Sector ───────────────────────────────────────────────────────
    if pe_vs_sector < 0.8:
        score += 5   # undervalued relative to sector
    elif pe_vs_sector > 1.5:
        score -= 5   # expensive relative to sector

    # ── Pledged shares ──────────────────────────────────────────────────────
    if pledged > 30:
        score -= 10  # governance risk

    # ── Analyst rating ──────────────────────────────────────────────────────
    if analyst <= 2:
        score += 6
    elif analyst >= 4:
        score -= 6

    # ── Dividend yield bonus ────────────────────────────────────────────────
    if div_yield > 2:
        score += 3

    # Clamp to [0, 100]
    return round(max(0.0, min(100.0, score)), 1)
