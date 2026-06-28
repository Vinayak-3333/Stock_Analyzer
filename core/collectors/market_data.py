"""
Optional keyed market-data providers.

These are used as fallback quote sources when NSE live quotes are blocked.
Each provider is intentionally small and returns the same quote shape consumed
by the modular pipeline.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import concurrent.futures
from datetime import datetime
from typing import Any

import requests

# Resolve the project root (2 levels up: collectors -> core -> project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

try:
    from dotenv import dotenv_values, load_dotenv

    # Load .env from project root regardless of CWD (backend is launched from backend/)
    _env_file = _PROJECT_ROOT / ".env"
    _env_example = _PROJECT_ROOT / ".env.nas.example"

    if _env_file.exists():
        load_dotenv(dotenv_path=str(_env_file), override=False)

    # Load real API keys from .env.nas.example for any key that is still unset/empty
    if _env_example.exists():
        for key, value in dotenv_values(str(_env_example)).items():
            if value and not os.environ.get(key):
                os.environ[key] = value
except Exception:
    pass

log = logging.getLogger("stockradar.collectors.market_data")

_TIMEOUT = 6
_UA = "StockRadarIN/1.0"


def _clean_number(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _pct_change(last_price: float | None, prev_close: float | None) -> float:
    if not last_price or not prev_close:
        return 0.0
    return round(((last_price - prev_close) / prev_close) * 100, 2)


def _quote_payload(symbol: str, source: str, data: dict) -> dict | None:
    last_price = _clean_number(data.get("lastPrice"))
    if last_price is None or last_price <= 0:
        return None

    prev_close = _clean_number(data.get("previousClose"), last_price)
    return {
        "symbol": symbol,
        "lastPrice": last_price,
        "open": _clean_number(data.get("open"), last_price),
        "dayHigh": _clean_number(data.get("dayHigh"), last_price),
        "dayLow": _clean_number(data.get("dayLow"), last_price),
        "previousClose": prev_close,
        "pChange": _clean_number(data.get("pChange"), _pct_change(last_price, prev_close)) or 0,
        "totalTradedVolume": _clean_number(data.get("totalTradedVolume")),
        "yearHigh": _clean_number(data.get("yearHigh")),
        "yearLow": _clean_number(data.get("yearLow")),
        "companyName": data.get("companyName") or symbol,
        "industry": data.get("industry") or "",
        "index": source,
        "fetched_at": datetime.now().isoformat(),
    }


def _get_json(url: str, params: dict) -> Any:
    response = requests.get(url, params=params, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
    if response.status_code != 200:
        log.debug("Provider request failed: %s %s", response.status_code, response.text[:120])
        return None
    try:
        return response.json()
    except ValueError:
        return None


def fetch_alpha_vantage_quote(symbol: str) -> dict | None:
    key = os.getenv("ALPHAVANTAGE_API_KEY") or os.getenv("ALPHA_VANTAGE_API_KEY")
    if not key:
        return None

    for provider_symbol in (f"{symbol}.BSE",):
        data = _get_json(
            "https://www.alphavantage.co/query",
            {"function": "GLOBAL_QUOTE", "symbol": provider_symbol, "apikey": key},
        )
        quote = (data or {}).get("Global Quote") or {}
        payload = _quote_payload(
            symbol,
            "ALPHA_VANTAGE",
            {
                "lastPrice": quote.get("05. price"),
                "open": quote.get("02. open"),
                "dayHigh": quote.get("03. high"),
                "dayLow": quote.get("04. low"),
                "previousClose": quote.get("08. previous close"),
                "pChange": str(quote.get("10. change percent", "")).replace("%", ""),
                "totalTradedVolume": quote.get("06. volume"),
            },
        )
        if payload:
            return payload
    return None


def fetch_finnhub_quote(symbol: str) -> dict | None:
    key = os.getenv("FINNHUB_API_KEY")
    if not key:
        return None

    for provider_symbol in (f"NSE:{symbol}", f"BSE:{symbol}"):
        quote = _get_json("https://finnhub.io/api/v1/quote", {"symbol": provider_symbol, "token": key}) or {}
        payload = _quote_payload(
            symbol,
            "FINNHUB",
            {
                "lastPrice": quote.get("c"),
                "open": quote.get("o"),
                "dayHigh": quote.get("h"),
                "dayLow": quote.get("l"),
                "previousClose": quote.get("pc"),
                "pChange": quote.get("dp"),
            },
        )
        if payload:
            return payload
    return None


def fetch_twelve_data_quote(symbol: str) -> dict | None:
    key = os.getenv("TWELVE_DATA_API_KEY")
    if not key:
        return None

    for provider_symbol in (f"{symbol}:NSE", f"{symbol}:BSE"):
        quote = _get_json("https://api.twelvedata.com/quote", {"symbol": provider_symbol, "apikey": key}) or {}
        if quote.get("status") == "error":
            continue
        payload = _quote_payload(
            symbol,
            "TWELVE_DATA",
            {
                "lastPrice": quote.get("close"),
                "open": quote.get("open"),
                "dayHigh": quote.get("high"),
                "dayLow": quote.get("low"),
                "previousClose": quote.get("previous_close"),
                "pChange": quote.get("percent_change"),
                "totalTradedVolume": quote.get("volume"),
                "companyName": quote.get("name"),
            },
        )
        if payload:
            return payload
    return None


def fetch_fmp_quote(symbol: str) -> dict | None:
    key = os.getenv("FMP_API_KEY") or os.getenv("FINANCIAL_MODELING_PREP_API_KEY")
    if not key:
        return None

    for provider_symbol in (f"{symbol}.NS", f"{symbol}.BO"):
        data = _get_json(f"https://financialmodelingprep.com/api/v3/quote/{provider_symbol}", {"apikey": key})
        quote = data[0] if isinstance(data, list) and data else {}
        payload = _quote_payload(
            symbol,
            "FMP",
            {
                "lastPrice": quote.get("price"),
                "open": quote.get("open"),
                "dayHigh": quote.get("dayHigh"),
                "dayLow": quote.get("dayLow"),
                "previousClose": quote.get("previousClose"),
                "pChange": quote.get("changesPercentage"),
                "totalTradedVolume": quote.get("volume"),
                "yearHigh": quote.get("yearHigh"),
                "yearLow": quote.get("yearLow"),
                "companyName": quote.get("name"),
            },
        )
        if payload:
            return payload
    return None


_PROVIDERS = {
    "alpha_vantage": fetch_alpha_vantage_quote,
    "finnhub": fetch_finnhub_quote,
    "twelve_data": fetch_twelve_data_quote,
    "fmp": fetch_fmp_quote,
}


def configured_provider_names() -> list[str]:
    raw = os.getenv("MARKET_DATA_PROVIDER_ORDER", "twelve_data,alpha_vantage,fmp,finnhub")
    return [name.strip().lower() for name in raw.split(",") if name.strip()]


def fetch_keyed_quote(symbol: str) -> dict | None:
    for provider_name in configured_provider_names():
        provider = _PROVIDERS.get(provider_name)
        if not provider:
            continue
        try:
            quote = provider(symbol)
            if quote:
                return quote
        except Exception as exc:
            log.debug("%s quote failed for %s: %s", provider_name, symbol, exc)
    return None


def collect_keyed_quotes(symbols: list[str]) -> dict[str, dict]:
    quotes: dict[str, dict] = {}
    max_workers = min(8, max(1, len(symbols)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_keyed_quote, symbol): symbol for symbol in symbols}
        for future in concurrent.futures.as_completed(futures):
            symbol = futures[future]
            try:
                quote = future.result()
                if quote:
                    quotes[symbol] = quote
            except Exception as exc:
                log.debug("Keyed quote collection failed for %s: %s", symbol, exc)
    log.info("Collected %d keyed-provider quotes", len(quotes))
    return quotes
