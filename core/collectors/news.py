"""
News Collector + FinBERT Sentiment Pipeline
============================================
Sources:
  - Google News RSS (free, no auth)
  - Economic Times RSS (free)
  - GDELT API (free, no auth — global news tone for India)
  - NewsAPI.org (free tier: 100 req/day — set NEWSAPI_KEY env var)

NLP Pipeline:
  1. Fetch headlines
  2. VADER quick score (instant, no model download)
  3. FinBERT score (async, requires transformers + torch)
  4. Event classification (earnings / M&A / regulatory / fraud)

Writes to: DuckDB raw_news table
Publishes to: Kafka topic news.raw and news.scored
"""

import os
import uuid
import logging
import json
import time
import re
import hashlib
from datetime import datetime, timedelta
from typing import Optional
import xml.etree.ElementTree as ET

import requests

log = logging.getLogger("stockradar.collectors.news")

NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")   # Set in env or .env file

# Event keywords for classification
_EVENT_PATTERNS = {
    "earnings":    r"\b(earnings|results|profit|revenue|EPS|quarterly|annual report)\b",
    "ma":          r"\b(acqui|merger|takeover|buyout|stake|acquisition|M&A)\b",
    "regulatory":  r"\b(SEBI|RBI|penalty|fine|ban|investigation|ED|CBI|fraud)\b",
    "fraud":       r"\b(fraud|scam|misappropriat|embezzle|default|bankrupt|NPA)\b",
    "policy":      r"\b(budget|tax|GST|policy|government|ministry|regulation|reform)\b",
}


# ── VADER (instant, no download required) ────────────────────────────────────

def _vader_score(text: str) -> float:
    """VADER compound sentiment (-1 negative, +1 positive)."""
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        vader = SentimentIntensityAnalyzer()
        return round(vader.polarity_scores(text)["compound"], 4)
    except ImportError:
        # Fallback: keyword-based simple scorer
        pos = sum(text.lower().count(w) for w in
                  ["growth", "profit", "surge", "gain", "strong", "beat", "high", "buy",
                   "record", "breakout", "acquisition", "expansion", "upgrade"])
        neg = sum(text.lower().count(w) for w in
                  ["loss", "fall", "decline", "drop", "weak", "miss", "low", "sell",
                   "fraud", "bankrupt", "penalty", "fine", "downgrade", "investigation"])
        total = pos + neg
        return round((pos - neg) / max(total, 1), 2)


# ── FinBERT (loads once, requires: pip install transformers torch) ────────────

_finbert_pipeline = None


def _get_finbert():
    global _finbert_pipeline
    if _finbert_pipeline is not None:
        return _finbert_pipeline
    try:
        from transformers import pipeline
        log.info("Loading FinBERT model (first time only)...")
        _finbert_pipeline = pipeline(
            "text-classification",
            model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert",
            truncation=True,
            max_length=512,
        )
        log.info("FinBERT loaded.")
    except Exception as e:
        log.warning("FinBERT not available (%s) — using VADER only", e)
        _finbert_pipeline = None
    return _finbert_pipeline


def _finbert_score(text: str) -> Optional[float]:
    """FinBERT score: positive=+1, negative=-1, neutral=0 (scaled by confidence)."""
    pipe = _get_finbert()
    if not pipe:
        return None
    try:
        result = pipe(text[:512])[0]
        label = result["label"].lower()
        score = result["score"]
        if label == "positive":
            return round(score, 4)
        elif label == "negative":
            return round(-score, 4)
        else:
            return 0.0
    except Exception:
        return None


# ── Event classification ──────────────────────────────────────────────────────

def classify_event(text: str) -> Optional[str]:
    """Return the first matching event type or None."""
    for event_type, pattern in _EVENT_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            return event_type
    return None


# ── Google News RSS ───────────────────────────────────────────────────────────

def fetch_google_news(query: str, max_items: int = 20) -> list[dict]:
    """Fetch news from Google News RSS. No auth required."""
    encoded = requests.utils.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}+india+stock+NSE&hl=en-IN&gl=IN&ceid=IN:en"
    articles = []
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(r.content)
        items = root.findall(".//item")[:max_items]
        for item in items:
            title = item.findtext("title", "")
            pub   = item.findtext("pubDate", "")
            link  = item.findtext("link", "")
            articles.append({
                "headline":    title,
                "url":         link,
                "source":      "google_news",
                "published_at": _parse_date(pub),
                "symbol":      query,
            })
    except Exception as e:
        log.debug("Google News RSS failed for '%s': %s", query, e)
    return articles


def fetch_economic_times_rss(max_items: int = 30) -> list[dict]:
    """Economic Times markets RSS — no auth."""
    urls = [
        "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    ]
    articles = []
    for url in urls:
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:max_items]:
                title = item.findtext("title", "")
                pub   = item.findtext("pubDate", "")
                link  = item.findtext("link", "")
                desc  = item.findtext("description", "")
                articles.append({
                    "headline":    title,
                    "url":         link,
                    "source":      "economic_times",
                    "published_at": _parse_date(pub),
                    "symbol":      None,  # will be matched by keyword
                    "summary":     re.sub(r"<[^>]+>", "", desc)[:500],
                })
        except Exception as e:
            log.debug("ET RSS failed: %s", e)
    return articles


def fetch_gdelt_india(hours_back: int = 6) -> list[dict]:
    """
    GDELT API — completely free, no auth.
    Returns global news events related to India markets with tone scores.
    """
    since = (datetime.utcnow() - timedelta(hours=hours_back)).strftime("%Y%m%d%H%M%S")
    url = (
        f"https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query=india+stock+market+NSE+BSE+SEBI"
        f"&mode=artlist&maxrecords=25&startdatetime={since}"
        f"&format=json"
    )
    articles = []
    try:
        r = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            data = r.json()
            for item in (data.get("articles") or []):
                articles.append({
                    "headline":    item.get("title", ""),
                    "url":         item.get("url", ""),
                    "source":      "gdelt",
                    "published_at": item.get("seendate"),
                    "symbol":      None,
                    "gdelt_tone":  item.get("tone"),
                })
    except Exception as e:
        log.debug("GDELT failed: %s", e)
    return articles


def fetch_newsapi(query: str, max_items: int = 20) -> list[dict]:
    """NewsAPI.org — free tier 100 req/day. Requires NEWSAPI_KEY env var."""
    if not NEWSAPI_KEY:
        return []
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": f"{query} NSE India stock",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": max_items,
        "apiKey": NEWSAPI_KEY,
    }
    articles = []
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            for item in r.json().get("articles", []):
                articles.append({
                    "headline":    item.get("title", ""),
                    "url":         item.get("url", ""),
                    "source":      f"newsapi:{item.get('source', {}).get('name', '')}",
                    "published_at": item.get("publishedAt"),
                    "symbol":      query,
                    "summary":     item.get("description", "")[:500],
                })
    except Exception as e:
        log.debug("NewsAPI failed for '%s': %s", query, e)
    return articles


# ── Score articles (VADER + FinBERT) ─────────────────────────────────────────

def score_articles(articles: list[dict], use_finbert: bool = True) -> list[dict]:
    """Add sentiment scores and event classification to articles."""
    scored = []
    for art in articles:
        text = art.get("headline", "") + " " + art.get("summary", "")
        art["raw_sentiment"]  = _vader_score(text)
        art["finbert_score"]  = _finbert_score(text) if use_finbert else None
        art["event_type"]     = classify_event(text)
        art["id"]             = hashlib.md5(art.get("url", text).encode()).hexdigest()
        scored.append(art)
    return scored


# ── Symbol matching ───────────────────────────────────────────────────────────

def match_symbol(article: dict, symbol_names: dict) -> Optional[str]:
    """
    Try to match an article to a stock symbol by scanning headline for company names.
    symbol_names: {symbol: company_name}
    """
    text = (article.get("headline", "") + " " + article.get("summary", "")).upper()
    for sym, name in symbol_names.items():
        if sym.upper() in text or (name and name.upper()[:8] in text):
            return sym
    return None


# ── Save to lake ──────────────────────────────────────────────────────────────

def save_news_to_lake(articles: list[dict]):
    """Write scored articles to raw_news table."""
    from core.lake.manager import get_lake
    if not articles:
        return
    conn = get_lake()
    for art in articles:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO raw_news
                    (id, symbol, headline, summary, source, url, published_at,
                     raw_sentiment, finbert_score, event_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                art.get("id", str(uuid.uuid4())),
                art.get("symbol"),
                art.get("headline", "")[:500],
                art.get("summary", "")[:1000],
                art.get("source", ""),
                art.get("url", "")[:500],
                art.get("published_at"),
                art.get("raw_sentiment"),
                art.get("finbert_score"),
                art.get("event_type"),
            ])
        except Exception as e:
            log.debug("News insert failed: %s", e)
    conn.commit()
    log.info("Saved %d articles to lake", len(articles))


# ── Main collection run ───────────────────────────────────────────────────────

def collect_news(
    symbols: list[str] = None,
    symbol_names: dict = None,
    use_finbert: bool = False,   # False = faster (VADER only); True = slower but accurate
) -> list[dict]:
    """
    Collect news from all sources, score, match to symbols, save to lake.
    Returns list of scored articles.
    """
    all_articles = []

    # General India market news
    all_articles += fetch_economic_times_rss(30)
    all_articles += fetch_gdelt_india(hours_back=8)

    # Per-symbol news (top symbols only to avoid rate limits)
    if symbols:
        for sym in symbols[:15]:   # cap at 15 per run
            all_articles += fetch_google_news(sym, 5)
            if NEWSAPI_KEY:
                all_articles += fetch_newsapi(sym, 5)
            time.sleep(0.3)

    # Deduplicate by ID
    seen = set()
    unique = []
    for art in all_articles:
        art_id = hashlib.md5(art.get("url", art.get("headline", "")).encode()).hexdigest()
        art["id"] = art_id
        if art_id not in seen:
            seen.add(art_id)
            unique.append(art)

    # Match symbols for general articles
    if symbol_names:
        for art in unique:
            if not art.get("symbol"):
                art["symbol"] = match_symbol(art, symbol_names)

    # Score sentiment
    scored = score_articles(unique, use_finbert=use_finbert)

    # Save to lake
    save_news_to_lake(scored)
    log.info("News collection done: %d unique articles", len(scored))
    return scored


def get_symbol_sentiment(symbol: str, days_back: int = 3) -> dict:
    """
    Retrieve aggregated sentiment for a symbol from the lake.
    Returns: {avg_score, article_count, event_type, score_trend}
    """
    from core.lake.manager import get_lake
    conn = get_lake()
    try:
        result = conn.execute("""
            SELECT
                AVG(COALESCE(finbert_score, raw_sentiment)) AS avg_score,
                COUNT(*) AS article_count,
                MODE(event_type) AS top_event_type
            FROM raw_news
            WHERE symbol = ?
              AND published_at >= NOW() - INTERVAL (?) DAY
              AND headline IS NOT NULL
        """, [symbol, days_back]).fetchone()

        if result and result[0] is not None:
            return {
                "avg_score":     round(float(result[0]), 4),
                "article_count": int(result[1]),
                "event_type":    result[2],
                "sentiment_label": (
                    "POSITIVE" if result[0] > 0.15 else
                    "NEGATIVE" if result[0] < -0.15 else "NEUTRAL"
                ),
            }
    except Exception as e:
        log.debug("Sentiment query failed for %s: %s", symbol, e)

    return {"avg_score": 0.0, "article_count": 0, "event_type": None, "sentiment_label": "NEUTRAL"}


def _parse_date(date_str: str) -> Optional[str]:
    """Parse RSS date string to ISO format."""
    if not date_str:
        return None
    fmts = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y%m%d%H%M%S",
    ]
    for fmt in fmts:
        try:
            return datetime.strptime(date_str.strip(), fmt).isoformat()
        except Exception:
            continue
    return date_str


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from core.lake.schema import init_schema
    init_schema()
    articles = collect_news(symbols=["RELIANCE", "INFY", "HDFCBANK"], use_finbert=False)
    print(f"Collected {len(articles)} articles")
    for a in articles[:5]:
        print(f"  [{a.get('source')}] {a.get('headline', '')[:80]} | "
              f"sentiment={a.get('raw_sentiment')} | event={a.get('event_type')}")
