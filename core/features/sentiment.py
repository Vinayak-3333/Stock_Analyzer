"""
Sentiment Feature Engineering Module
=====================================
Computes sentiment features from multiple free news sources for a given
Indian stock symbol.  Designed to work standalone — no DuckDB lake, no API
keys, no heavy ML models required.

Sources (all free, no auth):
  1. Yahoo Finance news via ``yfinance.Ticker.news``
  2. Google News RSS (India locale)
  3. Economic Times RSS (market headlines)

NLP layers:
  - VADER compound sentiment (fast, lexicon-based)
  - Keyword-based scoring (mirrors Analyzer.py logic)
  - Event classification (earnings / acquisition / regulatory / …)

Usage::

    >>> from core.features.sentiment import compute_sentiment_features, compute_sentiment_score
    >>> feats = compute_sentiment_features("RELIANCE.NS", company_name="Reliance Industries")
    >>> score = compute_sentiment_score(feats)
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import List, Optional
from urllib.parse import quote_plus

from core.fetch import get_engine

log = logging.getLogger("stockradar.features.sentiment")

# ---------------------------------------------------------------------------
# VADER — lazy-loaded, gracefully optional
# ---------------------------------------------------------------------------
_vader_analyzer = None
_vader_available: Optional[bool] = None  # None = not yet checked


def _get_vader():
    """Return the shared VADER analyzer or *None* if the package is absent."""
    global _vader_analyzer, _vader_available
    if _vader_available is not None:
        return _vader_analyzer

    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        _vader_analyzer = SentimentIntensityAnalyzer()
        _vader_available = True
        log.debug("VADER loaded successfully")
    except ImportError:
        _vader_analyzer = None
        _vader_available = False
        log.info("vaderSentiment not installed — falling back to keyword-only scoring")
    return _vader_analyzer


def _vader_compound(text: str) -> Optional[float]:
    """VADER compound score for *text* (–1 … +1), or ``None`` if unavailable."""
    vader = _get_vader()
    if vader is None:
        return None
    try:
        return round(vader.polarity_scores(text)["compound"], 4)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Keyword tables (kept in sync with Analyzer.py _NEWS_POS_*/NEG_* lists)
# ---------------------------------------------------------------------------

_POS_STRONG: List[str] = [
    "order", "contract", "wins", "awarded", "record profit", "buyback",
    "acquisition", "beats estimates", "revenue surge", "strong results",
    "expansion", "partnership", "buy rating", "all time high", "dividend",
    "bonus share", "profit growth", "revenue growth", "order book", "approval",
]

_POS_MILD: List[str] = [
    "growth", "profit", "positive", "rally", "surge", "gain", "rise",
    "strong", "better", "outlook", "recovery", "momentum", "outperform",
]

_NEG_STRONG: List[str] = [
    "fraud", "scam", "penalty", "default", "probe", "sebi action",
    "fir", "arrest", "cancelled", "misses estimates", "write-off",
    "downgrade", "sell rating", "insolvency", "loss widens",
]

_NEG_MILD: List[str] = [
    "weak", "fall", "drop", "down", "concern", "delay", "pressure",
    "decline", "warning", "risk", "caution", "lower", "disappoints",
]


def _keyword_score_headline(title: str) -> int:
    """Score a single headline using keyword lists.

    Strong positive/negative keywords get ±2, mild ±1.
    Returns the cumulative integer score for *title*.
    """
    t = title.lower()
    s = 0
    for kw in _POS_STRONG:
        if kw in t:
            s += 2
    for kw in _POS_MILD:
        if kw in t:
            s += 1
    for kw in _NEG_STRONG:
        if kw in t:
            s -= 2
    for kw in _NEG_MILD:
        if kw in t:
            s -= 1
    return s


# ---------------------------------------------------------------------------
# Event detection
# ---------------------------------------------------------------------------

_EVENT_KEYWORDS: dict[str, list[str]] = {
    "earnings":    ["quarterly results", "earnings", "q1 results", "q2 results",
                    "q3 results", "q4 results", "annual results", "profit report"],
    "acquisition": ["acquisition", "acquires", "merger", "takeover", "buyout",
                    "stake purchase"],
    "regulatory":  ["sebi", "rbi action", "penalty", "regulatory", "investigation",
                    "compliance"],
    "fraud":       ["fraud", "scam", "misappropriation", "embezzlement",
                    "default", "fir filed", "arrested"],
    "dividend":    ["dividend", "bonus share", "buyback"],
    "order_win":   ["order win", "contract win", "order book", "awarded contract",
                    "new order", "wins order", "bags order"],
}


def _detect_event(headlines: List[str]) -> str:
    """Scan *headlines* for the most prominent event type.

    Returns one of: ``'earnings'``, ``'acquisition'``, ``'regulatory'``,
    ``'fraud'``, ``'dividend'``, ``'order_win'``, or ``'none'``.
    """
    counts: dict[str, int] = {k: 0 for k in _EVENT_KEYWORDS}
    combined = " ".join(headlines).lower()

    for event_type, keywords in _EVENT_KEYWORDS.items():
        for kw in keywords:
            counts[event_type] += combined.count(kw)

    best = max(counts, key=counts.get)  # type: ignore[arg-type]
    return best if counts[best] > 0 else "none"


# ---------------------------------------------------------------------------
# News fetchers (free, no auth)
# ---------------------------------------------------------------------------

def _fetch_yahoo_news(symbol: str, ticker: object = None) -> List[str]:
    """Fetch up to 15 headlines from Yahoo Finance via yfinance."""
    headlines: List[str] = []
    try:
        import yfinance as yf
        tk = ticker or yf.Ticker(symbol)
        news_items = get_engine().call(
            "yahoo", lambda: getattr(tk, "news", None) or []
        )
        for item in news_items[:15]:
            title = item.get("title", "")
            if title:
                headlines.append(title)
    except Exception as exc:
        log.debug("Yahoo Finance news fetch failed for %s: %s", symbol, exc)
    return headlines


def _fetch_google_news_rss(query: str) -> List[str]:
    """Fetch headlines from Google News RSS (India locale)."""
    headlines: List[str] = []
    try:
        q = quote_plus(f"{query} NSE stock India")
        url = (
            f"https://news.google.com/rss/search?q={q}"
            f"&hl=en-IN&gl=IN&ceid=IN:en"
        )
        resp = get_engine().get("google_news", url)
        root = ET.fromstring(resp.content)
        for item in root.findall(".//item")[:15]:
            el = item.find("title")
            if el is not None and el.text:
                headlines.append(el.text)
    except Exception as exc:
        log.debug("Google News RSS failed for '%s': %s", query, exc)
    return headlines


_ET_RSS_TTL_SECONDS = 900
_et_rss_cache: tuple[float, List[str]] | None = None


def _fetch_et_rss() -> List[str]:
    """Fetch general Indian market headlines from Economic Times RSS.

    The feed is market-wide, not per-symbol, so one fetch is cached
    in-process and shared by every symbol in a run.
    """
    global _et_rss_cache
    if _et_rss_cache is not None and time.time() - _et_rss_cache[0] < _ET_RSS_TTL_SECONDS:
        return _et_rss_cache[1]

    headlines: List[str] = []
    url = "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"
    try:
        resp = get_engine().get("et_rss", url)
        root = ET.fromstring(resp.content)
        for item in root.findall(".//item")[:20]:
            el = item.find("title")
            if el is not None and el.text:
                headlines.append(el.text)
    except Exception as exc:
        log.debug("Economic Times RSS failed: %s", exc)
    if headlines:
        _et_rss_cache = (time.time(), headlines)
    return headlines


# ---------------------------------------------------------------------------
# Main feature computation
# ---------------------------------------------------------------------------

def compute_sentiment_features(
    symbol: str,
    company_name: str = None,
    ticker: object = None,
) -> dict:
    """Compute a full sentiment feature dict for *symbol*.

    Parameters
    ----------
    symbol : str
        NSE symbol with or without ``.NS`` suffix (e.g. ``"RELIANCE.NS"``).
    company_name : str, optional
        Human-readable company name for better news search relevance.
    ticker : object, optional
        Pre-created ``yfinance.Ticker`` instance to avoid duplicate HTTP
        calls when the caller already holds one.

    Returns
    -------
    dict
        Keys: ``vader_score``, ``keyword_score``, ``headline_count``,
        ``positive_ratio``, ``negative_ratio``, ``event_type``,
        ``buzz_factor``, ``top_headlines``, ``sentiment_label``.
    """
    # -- defaults (returned on total failure) --------------------------------
    defaults: dict = {
        "vader_score":      0.0,
        "keyword_score":    0,
        "headline_count":   0,
        "positive_ratio":   0.0,
        "negative_ratio":   0.0,
        "event_type":       "none",
        "buzz_factor":      0.0,
        "top_headlines":    [],
        "sentiment_label":  "NEUTRAL",
    }

    nse_code = symbol.replace(".NS", "")
    name_q = company_name or nse_code

    # -- collect headlines from all sources ----------------------------------
    headlines: List[str] = []

    # 1) Yahoo Finance
    headlines.extend(_fetch_yahoo_news(symbol, ticker=ticker))

    # 2) Google News RSS
    headlines.extend(_fetch_google_news_rss(name_q))

    # 3) Economic Times (general market — we filter for relevance below)
    et_headlines = _fetch_et_rss()
    # Keep only ET headlines mentioning the company / symbol
    name_lower = name_q.lower()
    code_lower = nse_code.lower()
    for h in et_headlines:
        hl = h.lower()
        if code_lower in hl or name_lower in hl:
            headlines.append(h)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: List[str] = []
    for h in headlines:
        key = h.strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(h)
    headlines = unique

    if not headlines:
        log.info("No headlines found for %s — returning neutral defaults", symbol)
        return defaults

    # -- VADER scores --------------------------------------------------------
    vader_scores: List[float] = []
    for h in headlines:
        v = _vader_compound(h)
        if v is not None:
            vader_scores.append(v)

    vader_avg = (
        round(sum(vader_scores) / len(vader_scores), 4)
        if vader_scores
        else 0.0
    )

    # -- Keyword scores ------------------------------------------------------
    kw_scores = [_keyword_score_headline(h) for h in headlines]
    keyword_total = sum(kw_scores)

    # -- Positive / Negative ratios ------------------------------------------
    positive_count = sum(1 for s in kw_scores if s > 0)
    negative_count = sum(1 for s in kw_scores if s < 0)
    n = len(headlines)
    positive_ratio = round(positive_count / n, 4)
    negative_ratio = round(negative_count / n, 4)

    # Use VADER ratios when available (more nuanced)
    if vader_scores:
        positive_count_v = sum(1 for v in vader_scores if v > 0.05)
        negative_count_v = sum(1 for v in vader_scores if v < -0.05)
        positive_ratio = round(positive_count_v / len(vader_scores), 4)
        negative_ratio = round(negative_count_v / len(vader_scores), 4)

    # -- Event detection -----------------------------------------------------
    event_type = _detect_event(headlines)

    # -- Buzz factor (normalised headline count, 0-1) ------------------------
    buzz_factor = round(min(n / 20.0, 1.0), 4)

    # -- Top impactful headlines (3 best + 1 worst) --------------------------
    paired = sorted(zip(kw_scores, headlines), key=lambda x: x[0], reverse=True)
    top_pos = [h for s, h in paired if s > 0][:3]
    top_neg = [h for s, h in paired if s < 0][:1]
    top_headlines = (top_pos + top_neg)[:4]

    # If keyword scoring found nothing, fall back to VADER-ordered list
    if not top_headlines and vader_scores:
        vader_paired = sorted(
            zip(vader_scores, headlines[:len(vader_scores)]),
            key=lambda x: abs(x[0]),
            reverse=True,
        )
        top_headlines = [h for _, h in vader_paired][:4]

    # -- Overall sentiment label ---------------------------------------------
    # Combine VADER + keyword for robust labelling
    if vader_scores:
        label_score = vader_avg
    else:
        avg_kw = keyword_total / n if n else 0
        label_score = avg_kw / 3.0  # normalise to roughly –1…+1 range

    if label_score >= 0.15:
        sentiment_label = "POSITIVE"
    elif label_score <= -0.15:
        sentiment_label = "NEGATIVE"
    else:
        sentiment_label = "NEUTRAL"

    return {
        "vader_score":      vader_avg,
        "keyword_score":    keyword_total,
        "headline_count":   n,
        "positive_ratio":   positive_ratio,
        "negative_ratio":   negative_ratio,
        "event_type":       event_type,
        "buzz_factor":      buzz_factor,
        "top_headlines":    top_headlines,
        "sentiment_label":  sentiment_label,
    }


# ---------------------------------------------------------------------------
# Scoring function  (features dict → 0-100 score)
# ---------------------------------------------------------------------------

def compute_sentiment_score(features: dict) -> float:
    """Convert a sentiment feature dict into a 0–100 numeric score.

    Parameters
    ----------
    features : dict
        Output of :func:`compute_sentiment_features`.

    Returns
    -------
    float
        Score in the range ``[0, 100]``, rounded to 1 decimal.

    Scoring breakdown
    -----------------
    * **VADER contribution** (up to ±12 pts)
    * **Keyword contribution** (up to ±10 pts)
    * **Event bonus** (up to ±15 pts)
    * **Buzz factor** (up to ±3 pts)
    * **Base**: 50
    """
    score: float = 50.0

    # -- VADER contribution --------------------------------------------------
    vader = features.get("vader_score", 0.0) or 0.0
    if vader > 0.3:
        score += 12
    elif vader > 0.1:
        score += 6
    elif vader < -0.3:
        score -= 12
    elif vader < -0.1:
        score -= 6

    # -- Keyword contribution ------------------------------------------------
    kw = features.get("keyword_score", 0) or 0
    if kw >= 6:
        score += 10
    elif kw >= 3:
        score += 6
    elif kw >= 1:
        score += 3
    elif kw <= -6:
        score -= 10
    elif kw <= -3:
        score -= 6
    elif kw <= -1:
        score -= 3

    # -- Event bonus/penalty -------------------------------------------------
    event = features.get("event_type", "none") or "none"

    # For earnings, refine using headline sentiment direction
    if event == "earnings":
        # Check whether headlines indicate beat or miss
        combined = " ".join(features.get("top_headlines", [])).lower()
        if any(kw in combined for kw in ("beats", "beat", "strong", "record")):
            score += 8   # earnings beat
        elif any(kw in combined for kw in ("misses", "miss", "disappoints", "weak")):
            score -= 8   # earnings miss
        else:
            score += 2   # neutral earnings mention
    elif event == "order_win":
        score += 6
    elif event == "acquisition":
        score += 5
    elif event == "dividend":
        score += 4
    elif event == "fraud":
        score -= 15
    elif event == "regulatory":
        score -= 10

    # -- Buzz factor ---------------------------------------------------------
    buzz = features.get("buzz_factor", 0.0) or 0.0
    if buzz > 0.8:
        score += 3   # high news coverage
    elif buzz < 0.2:
        score -= 2   # suspiciously low coverage

    # -- Clamp to [0, 100] ---------------------------------------------------
    return round(max(0.0, min(100.0, score)), 1)


# ---------------------------------------------------------------------------
# Convenience: single-call for symbol → score
# ---------------------------------------------------------------------------

def get_sentiment_score(
    symbol: str,
    company_name: str = None,
    ticker: object = None,
) -> tuple[dict, float]:
    """One-call convenience: compute features *and* score.

    Returns
    -------
    tuple[dict, float]
        ``(features_dict, score_0_to_100)``
    """
    feats = compute_sentiment_features(symbol, company_name=company_name, ticker=ticker)
    score = compute_sentiment_score(feats)
    return feats, score


# ---------------------------------------------------------------------------
# CLI test harness
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    sym = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE.NS"
    name = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"\n{'='*60}")
    print(f"  Sentiment Analysis — {sym}")
    print(f"{'='*60}\n")

    feats, score = get_sentiment_score(sym, company_name=name)

    print(f"  Headlines found  : {feats['headline_count']}")
    print(f"  VADER score      : {feats['vader_score']:+.4f}")
    print(f"  Keyword score    : {feats['keyword_score']:+d}")
    print(f"  Positive ratio   : {feats['positive_ratio']:.1%}")
    print(f"  Negative ratio   : {feats['negative_ratio']:.1%}")
    print(f"  Event detected   : {feats['event_type']}")
    print(f"  Buzz factor      : {feats['buzz_factor']:.2f}")
    print(f"  Sentiment label  : {feats['sentiment_label']}")
    print(f"  ── Score         : {score}/100")

    if feats["top_headlines"]:
        print(f"\n  Top headlines:")
        for i, h in enumerate(feats["top_headlines"], 1):
            print(f"    {i}. {h[:100]}")

    print()
