"""
Screener.in Fundamentals Collector
====================================
Scrapes Screener.in for:
  - P/E, P/B, ROE, ROCE, Debt/Equity, Current Ratio
  - EPS growth (1Y, 3Y), Revenue growth (1Y, 3Y)
  - Promoter holding %, Pledged shares %
  - Free Cash Flow yield, Market Cap (Cr)
  - Dividend yield

No API key needed — Screener.in is public.
Rate limit: 1 request per 2 seconds to be polite.

Also fetches analyst ratings from yfinance as fallback.
"""

import logging
import time
import re
import json
from datetime import date, datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup
import yfinance as yf
import pandas as pd

log = logging.getLogger("stockradar.collectors.fundamentals")

_SCREENER_BASE = "https://www.screener.in/company"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _get_number(text: str) -> Optional[float]:
    """Extract first number from a text string."""
    if not text:
        return None
    text = text.strip().replace(",", "").replace("%", "").replace("Cr", "").strip()
    m = re.search(r"-?\d+\.?\d*", text)
    return float(m.group()) if m else None


def fetch_screener(symbol: str, series: str = "consolidated") -> dict:
    """
    Scrape Screener.in for a symbol.
    Returns dict with fundamental ratios or empty dict on failure.
    """
    url = f"{_SCREENER_BASE}/{symbol}/{series}/"
    result = {"symbol": symbol, "as_of_date": date.today().isoformat(), "source": "screener"}

    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=15)
        if r.status_code == 404:
            # Try standalone
            r = requests.get(
                f"{_SCREENER_BASE}/{symbol}/",
                headers={"User-Agent": _UA}, timeout=15
            )
        if r.status_code != 200:
            return result

        soup = BeautifulSoup(r.text, "html.parser")

        # ── Key ratios from the ratio section ──────────────────────────────
        ratio_section = soup.find("section", id="top-ratios")
        if ratio_section:
            for li in ratio_section.find_all("li"):
                name_tag = li.find("span", class_="name")
                val_tag  = li.find("span", class_="value") or li.find("span", class_="number")
                if not name_tag or not val_tag:
                    continue
                name = name_tag.get_text(strip=True).lower()
                val  = val_tag.get_text(strip=True)

                if "p/e" in name and "pe" not in result:
                    result["pe_ratio"] = _get_number(val)
                elif "price to book" in name or "p/b" in name:
                    result["pb_ratio"] = _get_number(val)
                elif "return on equity" in name or "roe" in name:
                    result["roe"] = _get_number(val)
                elif "return on capital" in name or "roce" in name:
                    result["roce"] = _get_number(val)
                elif "debt / equity" in name or "debt/equity" in name:
                    result["debt_to_equity"] = _get_number(val)
                elif "current ratio" in name:
                    result["current_ratio"] = _get_number(val)
                elif "dividend yield" in name:
                    result["div_yield"] = _get_number(val)
                elif "market cap" in name:
                    result["market_cap_cr"] = _get_number(val)

        # ── Promoter holding from shareholding section ──────────────────────
        sh_section = soup.find("section", id="shareholding")
        if sh_section:
            rows = sh_section.find_all("tr")
            for row in rows:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if cells and "promoter" in cells[0].lower():
                    # Last column is most recent quarter
                    pct_text = next((c for c in reversed(cells[1:]) if c), "")
                    result["promoter_holding"] = _get_number(pct_text)
                    break

        # ── Pledged shares (from notes or dedicated row) ────────────────────
        pledged_tag = soup.find(string=re.compile(r"pledg", re.IGNORECASE))
        if pledged_tag:
            # Walk up to find a number nearby
            parent = pledged_tag.parent
            for _ in range(4):
                if parent is None:
                    break
                text = parent.get_text()
                m = re.search(r"(\d+\.?\d*)\s*%", text)
                if m:
                    result["pledged_pct"] = float(m.group(1))
                    break
                parent = parent.parent

        log.debug("Screener %s: PE=%.1f ROE=%.1f promoter=%.1f%%",
                  symbol,
                  result.get("pe_ratio") or 0,
                  result.get("roe") or 0,
                  result.get("promoter_holding") or 0)

    except Exception as e:
        log.debug("Screener scrape failed for %s: %s", symbol, e)

    return result


def fetch_yfinance_fundamentals(symbol_ns: str) -> dict:
    """
    Fetch fundamentals from yfinance as supplement/fallback.
    symbol_ns: e.g. 'RELIANCE.NS'
    Returns dict with growth metrics not easily available from Screener.
    """
    result = {}
    try:
        tk = yf.Ticker(symbol_ns)
        info = tk.info or {}

        result["pe_ratio"]          = info.get("trailingPE") or info.get("forwardPE")
        result["pb_ratio"]          = info.get("priceToBook")
        result["roe"]               = (info.get("returnOnEquity") or 0) * 100 if info.get("returnOnEquity") else None
        result["div_yield"]         = (info.get("dividendYield") or 0) * 100 if info.get("dividendYield") else None
        result["market_cap_cr"]     = (info.get("marketCap") or 0) / 1e7  # Convert INR to Crores
        result["revenue_growth_1y"] = (info.get("revenueGrowth") or 0) * 100 if info.get("revenueGrowth") else None
        result["eps_growth_1y"]     = (info.get("earningsGrowth") or 0) * 100 if info.get("earningsGrowth") else None
        result["debt_to_equity"]    = info.get("debtToEquity")
        result["current_ratio"]     = info.get("currentRatio")
        result["fcf_yield"]         = None   # Calculated below

        # FCF yield = Free Cash Flow / Market Cap
        mcap = info.get("marketCap")
        fcf  = info.get("freeCashflow")
        if mcap and fcf and mcap > 0:
            result["fcf_yield"] = round(fcf / mcap * 100, 2)

        result["analyst_rating"] = info.get("recommendationMean")  # 1=StrongBuy..5=StrongSell

    except Exception as e:
        log.debug("yfinance fundamentals failed for %s: %s", symbol_ns, e)

    return result


def fetch_fundamentals_combined(symbol: str) -> dict:
    """
    Combine Screener.in (better for Indian ratios) with yfinance (growth metrics).
    Screener takes priority for ratio fields; yfinance fills gaps.
    """
    screener_data = fetch_screener(symbol)
    time.sleep(1.5)   # be polite to Screener
    yf_data = fetch_yfinance_fundamentals(f"{symbol}.NS")

    merged = {**yf_data, **screener_data}  # Screener overwrites yfinance where available
    merged["symbol"]      = symbol
    merged["as_of_date"]  = date.today().isoformat()
    merged["source"]      = "screener+yfinance"
    return merged


def save_fundamentals_to_lake(record: dict):
    """Upsert fundamentals record into raw_fundamentals table."""
    from core.lake.manager import get_lake
    conn = get_lake()
    conn.execute("""
        INSERT OR REPLACE INTO raw_fundamentals
            (symbol, as_of_date, pe_ratio, pb_ratio, roe, roce, debt_to_equity,
             current_ratio, fcf_yield, eps_growth_1y, revenue_growth_1y,
             promoter_holding, pledged_pct, market_cap_cr, div_yield, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        record.get("symbol"), record.get("as_of_date"),
        record.get("pe_ratio"), record.get("pb_ratio"),
        record.get("roe"), record.get("roce"),
        record.get("debt_to_equity"), record.get("current_ratio"),
        record.get("fcf_yield"), record.get("eps_growth_1y"),
        record.get("revenue_growth_1y"), record.get("promoter_holding"),
        record.get("pledged_pct"), record.get("market_cap_cr"),
        record.get("div_yield"), record.get("source", "screener+yfinance"),
    ])
    conn.commit()


def collect_fundamentals_batch(symbols: list[str]) -> list[dict]:
    """
    Fetch and save fundamentals for a batch of symbols.
    Rate-limited: 1.5s between Screener requests.
    """
    results = []
    for i, sym in enumerate(symbols):
        log.info("  [%d/%d] Fundamentals: %s", i + 1, len(symbols), sym)
        rec = fetch_fundamentals_combined(sym)
        save_fundamentals_to_lake(rec)
        results.append(rec)
    log.info("Fundamentals collected for %d symbols", len(results))
    return results


def get_fundamentals_from_lake(symbol: str) -> dict:
    """Retrieve latest fundamentals for a symbol from the lake."""
    from core.lake.manager import get_lake
    conn = get_lake()
    try:
        row = conn.execute("""
            SELECT * FROM raw_fundamentals
            WHERE symbol = ?
            ORDER BY as_of_date DESC LIMIT 1
        """, [symbol]).fetchone()
        if row:
            cols = [d[0] for d in conn.description]
            return dict(zip(cols, row))
    except Exception as e:
        log.debug("Lake fundamentals query failed for %s: %s", symbol, e)
    return {}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from core.lake.schema import init_schema
    init_schema()

    for sym in ["RELIANCE", "INFY", "HDFCBANK"]:
        print(f"\n--- {sym} ---")
        data = fetch_fundamentals_combined(sym)
        for k, v in data.items():
            if v is not None:
                print(f"  {k}: {v}")
        save_fundamentals_to_lake(data)
