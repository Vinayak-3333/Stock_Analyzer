"""
NSE Data Collector
===================
Fetches all NSE data and writes to DuckDB lake + publishes to Kafka.

Data collected:
  - Real-time equity quotes (all index constituents)
  - Delivery % per symbol (from quote-equity endpoint)
  - FII/DII daily net flow
  - Option chain (PCR, max pain, OI buildup)
  - Daily bhavcopy CSV (full market EOD data)

Kafka topics published:
  - nse.quotes       — real-time LTP + OHLC
  - nse.fii_dii      — daily flow
  - nse.delivery     — delivery % per symbol
  - nse.options      — option chain summary
"""

import requests
import threading
import time
import json
import uuid
import logging
import io
import zipfile
from datetime import datetime, date, timedelta
from typing import Optional

import pandas as pd

from core.fetch import CircuitOpen, get_engine

log = logging.getLogger("stockradar.collectors.nse")

# ── NSE session (cookie-based) ────────────────────────────────────────────────
_NSE_BASE   = "https://www.nseindia.com"
_NSE_API    = "https://www.nseindia.com/api"
_UA         = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ── Broad + Sector indices for dynamic stock universe (~300-500 unique) ────────
NSE_INDEX_MAP = {
    # Broad market (core coverage — ~300 unique stocks)
    "NIFTY 50":            "NIFTY 50",
    "NIFTY NEXT 50":       "NIFTY NEXT 50",
    "NIFTY MIDCAP 50":     "NIFTY MIDCAP 50",
    "NIFTY MIDCAP 100":    "NIFTY MIDCAP 100",
    "NIFTY SMLCAP 50":     "NIFTY SMLCAP 50",
    "NIFTY SMLCAP 100":    "NIFTY SMLCAP 100",
    # Sector indices (add ~50-100 unique stocks not in broad indices)
    "NIFTY IT":            "NIFTY IT",
    "NIFTY BANK":          "NIFTY BANK",
    "NIFTY PHARMA":        "NIFTY PHARMA",
    "NIFTY AUTO":          "NIFTY AUTO",
    "NIFTY FMCG":          "NIFTY FMCG",
    "NIFTY METAL":         "NIFTY METAL",
    "NIFTY REALTY":        "NIFTY REALTY",
    "NIFTY ENERGY":        "NIFTY ENERGY",
    "NIFTY INFRA":         "NIFTY INFRA",
    "NIFTY MEDIA":         "NIFTY MEDIA",
    "NIFTY COMMODITIES":   "NIFTY COMMODITIES",
    "NIFTY CONSUMPTION":   "NIFTY CONSUMPTION",
    "NIFTY FIN SERVICE":   "NIFTY FIN SERVICE",
    "NIFTY HEALTHCARE":    "NIFTY HEALTHCARE",
    "NIFTY PVT BANK":      "NIFTY PVT BANK",
    "NIFTY PSU BANK":      "NIFTY PSU BANK",
    "NIFTY CPSE":          "NIFTY CPSE",
    "NIFTY MNC":           "NIFTY MNC",
    # Thematic (add a few more unique names)
    "NIFTY OIL AND GAS":   "NIFTY OIL AND GAS",
    "NIFTY GROWSECT 15":   "NIFTY GROWSECT 15",
}


def _make_session() -> requests.Session:
    session = requests.Session()
    headers = {
        "User-Agent":      _UA,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept":          "*/*",
        "Referer":         _NSE_BASE + "/",
    }
    session.headers.update(headers)
    try:
        session.get(_NSE_BASE, timeout=12)
        time.sleep(0.5)
    except Exception as e:
        log.warning("NSE session warm-up failed: %s", e)
    return session


def _nse_get(session: requests.Session, endpoint: str, params: dict = None, retries=2):
    url = f"{_NSE_API}/{endpoint}"
    engine = get_engine()

    def _attempt():
        r = session.get(url, params=params, timeout=12)
        if r.status_code == 200 and r.content:
            return r.json()
        if r.status_code == 401:
            # Cookie expired — refresh and let the engine's retry re-attempt
            session.get(_NSE_BASE, timeout=12)
        raise requests.HTTPError(f"{r.status_code} for {endpoint}", response=r)

    try:
        return engine.call("nse", _attempt, retries=retries)
    except CircuitOpen as e:
        log.debug("NSE get %s skipped: %s", endpoint, e)
    except Exception as e:
        log.debug("NSE get %s failed: %s", endpoint, e)
    return None


# ── Kafka producer (optional — falls back gracefully if Kafka not running) ────

def _get_producer():
    try:
        from kafka import KafkaProducer
        producer = KafkaProducer(
            bootstrap_servers="localhost:9092",
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
            linger_ms=50,
            compression_type="gzip",
        )
        return producer
    except Exception as e:
        log.debug("Kafka not available (%s) — lake-only mode", e)
        return None


_producer = None

def _publish(topic: str, data: dict):
    global _producer
    if _producer is None:
        _producer = _get_producer()
    if _producer:
        try:
            _producer.send(topic, data)
        except Exception as e:
            log.debug("Kafka publish failed: %s", e)


# ── 1. Equity Quotes ──────────────────────────────────────────────────────────

def fetch_index_quotes(session: requests.Session, index_name: str) -> list[dict]:
    """Fetch all constituents + live quote for a given NSE index."""
    data = _nse_get(session, "equity-stockIndices", {"index": index_name})
    if not data:
        return []
    return data.get("data", [])


def collect_all_quotes(session: requests.Session = None) -> dict:
    """
    Fetch live quotes for all configured indices.
    Returns: {symbol: quote_dict} with live LTP, OHLC, volume, FII data
    """
    if session is None:
        session = _make_session()

    all_quotes = {}
    for index_name in NSE_INDEX_MAP.keys():
        log.info("  Fetching %s...", index_name)
        stocks = fetch_index_quotes(session, index_name)
        time.sleep(0.6)
        for s in stocks:
            sym = s.get("symbol", "")
            if not sym or sym == index_name:
                continue
            quote = {
                "symbol":      sym,
                "lastPrice":   s.get("lastPrice"),
                "open":        s.get("open"),
                "dayHigh":     s.get("dayHigh"),
                "dayLow":      s.get("dayLow"),
                "previousClose": s.get("previousClose"),
                "pChange":     s.get("pChange"),
                "totalTradedVolume": s.get("totalTradedVolume"),
                "yearHigh":    s.get("yearHigh"),
                "yearLow":     s.get("yearLow"),
                "nearWKH":     s.get("nearWKH"),
                "nearWKL":     s.get("nearWKL"),
                "perChange365d": s.get("perChange365d"),
                "companyName": (s.get("meta") or {}).get("companyName", sym),
                "industry":    (s.get("meta") or {}).get("industry", ""),
                "isin":        (s.get("meta") or {}).get("isin", ""),
                "index":       index_name,
                "fetched_at":  datetime.now().isoformat(),
            }
            all_quotes[sym] = quote
            _publish("nse.quotes", quote)

    log.info("Collected %d live quotes", len(all_quotes))
    return all_quotes


# ── 2. Delivery Data ──────────────────────────────────────────────────────────

def fetch_delivery(session: requests.Session, symbol: str) -> Optional[dict]:
    """
    Fetch delivery % for a single symbol using NSE quote-equity endpoint.
    Returns dict with delivered_qty, delivery_pct or None.
    """
    data = _nse_get(session, "quote-equity", {"symbol": symbol})
    if not data:
        return None
    # securityWiseDP section has delivery data
    dp = data.get("securityWiseDP") or {}
    qty_traded    = dp.get("quantityTraded") or dp.get("tradedVolume")
    qty_delivered = dp.get("deliveryQuantity") or dp.get("deliverableQuantity")
    pct           = dp.get("deliveryToTradedQuantity")
    if qty_traded is None and pct is None:
        return None
    return {
        "symbol":        symbol,
        "date":          date.today().isoformat(),
        "traded_qty":    qty_traded,
        "delivered_qty": qty_delivered,
        "delivery_pct":  float(pct) if pct is not None else None,
    }


def collect_delivery_batch(symbols: list[str], session: requests.Session = None) -> list[dict]:
    """Fetch delivery data for a list of symbols. Rate-limited."""
    if session is None:
        session = _make_session()
    results = []
    for i, sym in enumerate(symbols):
        d = fetch_delivery(session, sym)
        if d:
            results.append(d)
            _publish("nse.delivery", d)
        if i % 10 == 9:
            time.sleep(0.8)   # rate-limit burst
    log.info("Collected delivery data for %d/%d symbols", len(results), len(symbols))
    return results


def save_delivery_to_lake(records: list[dict]):
    """Write delivery records to DuckDB raw_delivery table."""
    from core.lake.manager import get_lake
    if not records:
        return
    conn = get_lake()
    df = pd.DataFrame(records)
    conn.execute("""
        INSERT OR REPLACE INTO raw_delivery (symbol, date, traded_qty, delivered_qty, delivery_pct)
        SELECT symbol, date::DATE, traded_qty, delivered_qty, delivery_pct
        FROM df
    """)
    conn.commit()
    log.info("Saved %d delivery records to lake", len(records))


# ── 3. FII / DII Flow ─────────────────────────────────────────────────────────

def fetch_fii_dii(session: requests.Session = None) -> Optional[dict]:
    """
    Fetch today's FII/DII net buy/sell data.
    NSE endpoint: fiidiiTradeReact
    Returns dict with fii_net, dii_net etc.
    """
    if session is None:
        session = _make_session()
    data = _nse_get(session, "fiidiiTradeReact")
    if not data or not isinstance(data, list):
        return None

    result = {
        "date":     date.today().isoformat(),
        "fii_buy":  None, "fii_sell": None, "fii_net": None,
        "dii_buy":  None, "dii_sell": None, "dii_net": None,
    }
    for rec in data:
        category = str(rec.get("category", "")).upper()
        if "FII" in category or "FPI" in category:
            result["fii_buy"]  = _parse_crore(rec.get("buyValue"))
            result["fii_sell"] = _parse_crore(rec.get("sellValue"))
            result["fii_net"]  = _parse_crore(rec.get("netValue"))
        elif "DII" in category:
            result["dii_buy"]  = _parse_crore(rec.get("buyValue"))
            result["dii_sell"] = _parse_crore(rec.get("sellValue"))
            result["dii_net"]  = _parse_crore(rec.get("netValue"))

    log.info("FII net: %.0f Cr | DII net: %.0f Cr",
             result["fii_net"] or 0, result["dii_net"] or 0)
    _publish("nse.fii_dii", result)
    return result


def _parse_crore(val) -> Optional[float]:
    if val is None:
        return None
    try:
        s = str(val).replace(",", "").strip()
        return float(s)
    except Exception:
        return None


def save_fii_dii_to_lake(record: dict):
    """Upsert FII/DII record into raw_fii_dii table."""
    from core.lake.manager import get_lake
    if not record:
        return
    conn = get_lake()
    conn.execute("""
        INSERT OR REPLACE INTO raw_fii_dii
            (date, fii_buy, fii_sell, fii_net, dii_buy, dii_sell, dii_net)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [
        record["date"], record["fii_buy"], record["fii_sell"], record["fii_net"],
        record["dii_buy"], record["dii_sell"], record["dii_net"],
    ])
    conn.commit()


# ── 4. Option Chain ───────────────────────────────────────────────────────────

def fetch_option_chain(symbol: str, session: requests.Session = None) -> Optional[dict]:
    """
    Fetch full option chain for a symbol.
    Returns summary: {pcr, max_pain_strike, total_ce_oi, total_pe_oi, atm_strike}
    """
    if session is None:
        session = _make_session()

    endpoint = "option-chain-equities" if symbol != "NIFTY" else "option-chain-indices"
    data = _nse_get(session, endpoint, {"symbol": symbol})
    if not data:
        return None

    records   = data.get("records", {}).get("data", [])
    spot      = data.get("records", {}).get("underlyingValue")
    expiries  = data.get("records", {}).get("expiryDates", [])
    if not records or not expiries:
        return None

    # Use nearest expiry
    nearest_expiry = expiries[0]
    rows = [r for r in records if r.get("expiryDate") == nearest_expiry]

    total_ce_oi = sum(r.get("CE", {}).get("openInterest", 0) or 0 for r in rows)
    total_pe_oi = sum(r.get("PE", {}).get("openInterest", 0) or 0 for r in rows)
    pcr = round(total_pe_oi / total_ce_oi, 3) if total_ce_oi > 0 else None

    # Max pain: find strike where total payout is minimum
    max_pain = _calc_max_pain(rows)

    summary = {
        "symbol":          symbol,
        "snapshot_ts":     datetime.now().isoformat(),
        "expiry":          nearest_expiry,
        "total_ce_oi":     total_ce_oi,
        "total_pe_oi":     total_pe_oi,
        "pcr":             pcr,
        "max_pain_strike": max_pain,
        "atm_strike":      _nearest_strike(spot, rows),
        "spot_price":      spot,
    }
    _publish("nse.options", summary)
    return summary


def _nearest_strike(spot, rows):
    if not spot or not rows:
        return None
    strikes = [r.get("strikePrice") for r in rows if r.get("strikePrice")]
    if not strikes:
        return None
    return min(strikes, key=lambda x: abs(x - spot))


def _calc_max_pain(rows) -> Optional[float]:
    """
    Max pain = strike price where sum of expiry losses for option buyers is maximum
    (i.e., where option writers make most money).
    """
    strikes = sorted(set(r["strikePrice"] for r in rows if "strikePrice" in r))
    if not strikes:
        return None

    ce_oi = {r["strikePrice"]: r.get("CE", {}).get("openInterest", 0) or 0 for r in rows}
    pe_oi = {r["strikePrice"]: r.get("PE", {}).get("openInterest", 0) or 0 for r in rows}

    min_pain = float("inf")
    max_pain_strike = strikes[0]

    for test_strike in strikes:
        pain = 0
        for s in strikes:
            # CE holders lose if expires below their strike
            if test_strike < s:
                pain += ce_oi.get(s, 0) * (s - test_strike)
            # PE holders lose if expires above their strike
            if test_strike > s:
                pain += pe_oi.get(s, 0) * (test_strike - s)
        if pain < min_pain:
            min_pain = pain
            max_pain_strike = test_strike

    return max_pain_strike


def save_options_to_lake(summary: dict):
    from core.lake.manager import get_lake
    if not summary:
        return
    conn = get_lake()
    conn.execute("""
        INSERT OR REPLACE INTO raw_options_summary
            (symbol, snapshot_ts, expiry, total_ce_oi, total_pe_oi, pcr, max_pain_strike, atm_strike)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        summary["symbol"], summary["snapshot_ts"], summary.get("expiry"),
        summary["total_ce_oi"], summary["total_pe_oi"], summary["pcr"],
        summary["max_pain_strike"], summary["atm_strike"],
    ])
    conn.commit()


# ── 5. Bhavcopy (NSE end-of-day full dump) ───────────────────────────────────

def fetch_bhavcopy(target_date: date = None) -> Optional[pd.DataFrame]:
    """
    Downloads NSE bhavcopy CSV for a given date.
    Falls back to previous trading day if today's not available.
    Returns DataFrame with full market data.
    """
    if target_date is None:
        target_date = date.today()

    # Try last 5 days so Monday/post-holiday runs still find the last trading day's file
    for offset in range(5):
        d = target_date - timedelta(days=offset)
        url = (
            f"https://nsearchives.nseindia.com/products/content/"
            f"sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"
        )
        try:
            headers = {"User-Agent": _UA, "Referer": _NSE_BASE + "/"}
            r = get_engine().get("nse_archives", url, headers=headers, retries=0)
            if len(r.content) > 1000:
                df = pd.read_csv(io.StringIO(r.text))
                df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_")
                df["date"] = d.isoformat()
                log.info("Bhavcopy loaded for %s: %d rows", d, len(df))
                return df
        except Exception as e:
            log.debug("Bhavcopy %s failed: %s", d, e)

    log.warning("Bhavcopy not available for recent dates")
    return None


def save_bhavcopy_to_lake(df: pd.DataFrame):
    """Write bhavcopy data to raw_bhavcopy and extract delivery to raw_delivery."""
    from core.lake.manager import get_lake
    if df is None or df.empty:
        return
    conn = get_lake()

    # Map column names (NSE bhavcopy has different column names)
    col_map = {
        "symbol": "symbol", "series": "series",
        "open_price": "open", "high_price": "high",
        "low_price": "low", "close_price": "close",
        "last_price": "last", "prev_close": "prev_close",
        "tottrdqty": "traded_qty", "tottrdval": "traded_value",
        "totaltrades": "total_trades", "isin_code": "isin",
    }
    available = {k: v for k, v in col_map.items() if k in df.columns}
    df_mapped = df[list(available.keys())].rename(columns=available)
    df_mapped["date"] = df["date"]

    # Filter EQ series only
    if "series" in df_mapped.columns:
        df_mapped = df_mapped[df_mapped["series"].str.strip() == "EQ"]

    conn.execute("""
        INSERT OR REPLACE INTO raw_bhavcopy
            (symbol, date, series, open, high, low, close, prev_close, traded_qty, traded_value, total_trades, isin)
        SELECT symbol, date::DATE, series, open, high, low, close, prev_close,
               traded_qty, traded_value, total_trades, isin
        FROM df_mapped
    """)

    # Extract delivery if columns present
    if "deliv_qty" in df.columns and "tottrdqty" in df.columns:
        df["delivery_pct"] = (df["deliv_qty"] / df["tottrdqty"] * 100).round(2)
        df_del = df[["symbol", "date", "tottrdqty", "deliv_qty", "delivery_pct"]].copy()
        df_del.columns = ["symbol", "date", "traded_qty", "delivered_qty", "delivery_pct"]
        if "series" in df.columns:
            df_del = df_del[df["series"].str.strip() == "EQ"]
        conn.execute("""
            INSERT OR REPLACE INTO raw_delivery (symbol, date, traded_qty, delivered_qty, delivery_pct)
            SELECT symbol, date::DATE, traded_qty, delivered_qty, delivery_pct
            FROM df_del
        """)

    conn.commit()
    log.info("Saved bhavcopy (%d rows) to lake", len(df_mapped))


# ── Main collector entry point ────────────────────────────────────────────────

def run_full_collection(
    collect_quotes: bool = True,
    collect_delivery_sample: bool = True,   # delivery for top 50 (rate-limited)
    collect_fii: bool = True,
    collect_options_sample: bool = False,   # options for NIFTY + BANKNIFTY only
    collect_bhavcopy: bool = True,
) -> dict:
    """
    Run all NSE data collection in sequence.
    Returns summary dict with counts.
    """
    from core.lake.manager import get_lake
    from core.lake.schema import init_schema
    init_schema()

    session = _make_session()
    summary = {}

    if collect_quotes:
        quotes = collect_all_quotes(session)
        summary["quotes"] = len(quotes)
        # Save OHLCV snapshot to lake
        if quotes:
            rows = []
            for sym, q in quotes.items():
                if q.get("lastPrice"):
                    rows.append({
                        "symbol": sym, "date": date.today().isoformat(),
                        "open": q.get("open"), "high": q.get("dayHigh"),
                        "low": q.get("dayLow"), "close": q.get("lastPrice"),
                        "volume": q.get("totalTradedVolume"), "source": "nse_live",
                    })
            conn = get_lake()
            df = pd.DataFrame(rows)
            conn.execute("""
                INSERT OR REPLACE INTO raw_ohlcv (symbol, date, open, high, low, close, volume, source)
                SELECT symbol, date::DATE, open, high, low, close, volume, source FROM df
            """)
            conn.commit()

    if collect_fii:
        fii = fetch_fii_dii(session)
        save_fii_dii_to_lake(fii)
        summary["fii_dii"] = 1 if fii else 0

    if collect_delivery_sample and "quotes" in summary:
        # Collect delivery for all symbols (rate-limited)
        symbols = list(quotes.keys())[:60]  # cap at 60 to avoid throttle
        delivery_records = collect_delivery_batch(symbols, session)
        save_delivery_to_lake(delivery_records)
        summary["delivery"] = len(delivery_records)

    if collect_options_sample:
        for sym in ["NIFTY", "BANKNIFTY", "RELIANCE", "TCS"]:
            opt = fetch_option_chain(sym, session)
            save_options_to_lake(opt)
            time.sleep(1)
        summary["options"] = 4

    if collect_bhavcopy:
        bhav = fetch_bhavcopy()
        save_bhavcopy_to_lake(bhav)
        summary["bhavcopy"] = len(bhav) if bhav is not None else 0

    log.info("NSE collection complete: %s", summary)
    return summary


# ── 5b. F&O symbol universe ───────────────────────────────────────────────────
# Only ~180-220 NSE stocks have derivative contracts. Option-chain calls for
# anything else are guaranteed failures that burn retries and timeouts, so the
# pipeline gates per-symbol option fetches on this list.

_FNO_CACHE_TTL_SECONDS = 12 * 3600
_fno_cache: tuple[float, set[str]] | None = None
_fno_lock = threading.Lock()


def fetch_fno_symbols(session: requests.Session = None) -> set[str]:
    """
    Return the set of NSE symbols with F&O contracts, cached in-process for
    12 hours.  Primary source is the derivatives master (fo_mktlots.csv on
    the archives host); falls back to the live equity-derivatives master
    API.  Returns an empty set when both fail — callers should treat that
    as "unknown" rather than "no F&O".
    """
    global _fno_cache
    with _fno_lock:
        if _fno_cache is not None and time.time() - _fno_cache[0] < _FNO_CACHE_TTL_SECONDS:
            return _fno_cache[1]

    symbols: set[str] = set()

    # Primary: F&O market-lots CSV (static archives host, no cookies needed)
    try:
        r = get_engine().get(
            "nse_archives",
            "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv",
            headers={"User-Agent": _UA, "Referer": _NSE_BASE + "/"},
        )
        for line in r.text.splitlines()[1:]:
            parts = line.split(",")
            if len(parts) >= 2:
                sym = parts[1].strip()
                if sym and sym.upper() not in ("SYMBOL", ""):
                    symbols.add(sym)
    except Exception as e:
        log.debug("fo_mktlots.csv fetch failed: %s", e)

    # Fallback: live master API
    if not symbols:
        try:
            sess = session or _make_session()
            data = _nse_get(sess, "master-quote")
            if isinstance(data, list):
                symbols = {str(s).strip() for s in data if s}
        except Exception as e:
            log.debug("master-quote F&O fallback failed: %s", e)

    if symbols:
        with _fno_lock:
            _fno_cache = (time.time(), symbols)
        log.info("F&O universe: %d symbols", len(symbols))
    else:
        log.warning("F&O symbol list unavailable from all sources")
    return symbols


# ── 6. Dynamic symbol helpers (for pipeline cascading) ───────────────────────

def fetch_equity_symbols_from_bhavcopy(target_date: date = None) -> list[str]:
    """
    Download today's bhavcopy and extract all unique EQ-series symbols.
    Returns a list of NSE symbols (e.g. ['RELIANCE', 'TCS', ...]).
    This gives the full ~1,800+ NSE equity universe.
    """
    df = fetch_bhavcopy(target_date)
    if df is None or df.empty:
        return []
    # The downloaded file also carries OHLC + delivery for every symbol —
    # persist it so the feature layers can read delivery from the lake
    # instead of making per-symbol NSE calls.
    try:
        save_bhavcopy_to_lake(df)
    except Exception as exc:
        log.debug("Bhavcopy lake save failed: %s", exc)
    # Filter EQ series only (skip BE, BZ, derivatives, etc.)
    if "series" in df.columns:
        eq_df = df[df["series"].str.strip() == "EQ"]
    else:
        eq_df = df
    if "symbol" not in eq_df.columns:
        return []
    symbols = sorted(eq_df["symbol"].str.strip().unique().tolist())
    log.info("Bhavcopy yielded %d EQ symbols", len(symbols))
    return symbols


def fetch_cached_symbols_from_lake(max_age_days: int = 7) -> list[str]:
    """
    Query the DuckDB lake for symbols seen in recent runs.
    Falls back to raw_ohlcv / raw_bhavcopy if known_symbols is empty.
    Returns a list of NSE symbols.
    """
    try:
        from core.lake.manager import get_lake
        conn = get_lake()

        # Try known_symbols table first
        try:
            result = conn.execute(
                "SELECT symbol FROM known_symbols "
                "WHERE last_seen >= current_date - ? "
                "ORDER BY symbol",
                [max_age_days],
            ).fetchall()
            if result:
                symbols = [row[0] for row in result]
                log.info("Lake known_symbols: %d symbols (last %d days)", len(symbols), max_age_days)
                return symbols
        except Exception as exc:
            log.debug("known_symbols query failed: %s", exc)  # table may not exist yet

        # Fallback: distinct symbols from raw_ohlcv
        try:
            result = conn.execute(
                "SELECT DISTINCT symbol FROM raw_ohlcv "
                "WHERE date >= current_date - ? "
                "ORDER BY symbol",
                [max_age_days],
            ).fetchall()
            if result:
                symbols = [row[0] for row in result]
                log.info("Lake raw_ohlcv fallback: %d symbols", len(symbols))
                return symbols
        except Exception as exc:
            log.debug("raw_ohlcv symbol query failed: %s", exc)

        # Fallback: distinct symbols from raw_bhavcopy
        try:
            result = conn.execute(
                "SELECT DISTINCT symbol FROM raw_bhavcopy "
                "WHERE date >= current_date - ? "
                "  AND series = 'EQ' "
                "ORDER BY symbol",
                [max_age_days],
            ).fetchall()
            if result:
                symbols = [row[0] for row in result]
                log.info("Lake raw_bhavcopy fallback: %d symbols", len(symbols))
                return symbols
        except Exception as exc:
            log.debug("raw_bhavcopy symbol query failed: %s", exc)

    except Exception as exc:
        log.debug("Lake symbol cache query failed: %s", exc)
    return []


def save_known_symbols_to_lake(symbols: list[str], source: str = "nse_index") -> None:
    """
    Persist a symbol list to the known_symbols table.
    Called after every successful symbol resolution so the next run
    always has a cached fallback.
    """
    if not symbols:
        return
    try:
        from core.lake.manager import get_lake
        conn = get_lake()
        today = date.today().isoformat()
        df = pd.DataFrame({
            "symbol": symbols,
            "exchange": "NSE",
            "series": "EQ",
            "last_seen": today,
            "source": source,
        })
        conn.execute("""
            INSERT OR REPLACE INTO known_symbols (symbol, exchange, series, last_seen, source)
            SELECT symbol, exchange, series, last_seen::DATE, source FROM df
        """)
        conn.commit()
        log.info("Saved %d symbols to known_symbols (source=%s)", len(symbols), source)
    except Exception as exc:
        log.debug("Failed to save known symbols: %s", exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_full_collection()
    print("Collection result:", result)
