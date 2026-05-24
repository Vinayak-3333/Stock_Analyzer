"""
Global Macro Data Collector
============================
Fetches: Crude oil, USD/INR, US indices, India VIX, US 10Y bond yield.
Source: Yahoo Finance (yfinance) — free, no auth required.
Writes to DuckDB raw_macro table and publishes to Kafka.
"""

import logging
import json
from datetime import date, datetime
from typing import Optional

import yfinance as yf
import pandas as pd

log = logging.getLogger("stockradar.collectors.global_data")

# Yahoo Finance symbols
MACRO_SYMBOLS = {
    "crude_usd":    "CL=F",    # WTI Crude Oil Futures (USD/barrel)
    "usdinr":       "USDINR=X", # USD/INR exchange rate
    "sp500_change": "^GSPC",   # S&P 500
    "dow_change":   "^DJI",    # Dow Jones
    "nasdaq_change":"^IXIC",   # NASDAQ
    "india_vix":    "^INDIAVIX", # India VIX
    "us_10y_yield": "^TNX",    # US 10-year Treasury yield
}


def fetch_macro_snapshot() -> dict:
    """
    Fetch latest macro data for all tracked symbols.
    Returns dict with current values + 1-day change %.
    """
    result = {
        "date":          date.today().isoformat(),
        "fetched_at":    datetime.now().isoformat(),
        "crude_usd":     None,
        "usdinr":        None,
        "sp500_change":  None,
        "dow_change":    None,
        "nasdaq_change": None,
        "india_vix":     None,
        "us_10y_yield":  None,
    }

    for field, ticker_sym in MACRO_SYMBOLS.items():
        try:
            tk = yf.Ticker(ticker_sym)
            hist = tk.history(period="2d", interval="1d", auto_adjust=True)
            if hist is None or len(hist) < 1:
                continue
            close = hist["Close"].dropna()
            if len(close) == 0:
                continue
            latest = float(close.iloc[-1])

            # For indices, store daily % change; for rates/commodities, store raw value
            if field in ("sp500_change", "dow_change", "nasdaq_change"):
                if len(close) >= 2:
                    prev = float(close.iloc[-2])
                    result[field] = round((latest - prev) / prev * 100, 3) if prev else None
                else:
                    result[field] = 0.0
            else:
                result[field] = round(latest, 4)

        except Exception as e:
            log.debug("Macro fetch failed for %s (%s): %s", field, ticker_sym, e)

    log.info(
        "Macro: Crude=%.1f | USD/INR=%.2f | VIX=%.2f | S&P500=%.2f%%",
        result["crude_usd"] or 0, result["usdinr"] or 0,
        result["india_vix"] or 0, result["sp500_change"] or 0,
    )
    return result


def fetch_macro_history(days: int = 365) -> pd.DataFrame:
    """
    Fetch historical macro data for backtesting feature engineering.
    Returns DataFrame with one row per date, columns for each macro field.
    """
    period = f"{days}d"
    dfs = {}
    for field, sym in MACRO_SYMBOLS.items():
        try:
            hist = yf.download(sym, period=period, interval="1d",
                               progress=False, auto_adjust=True)
            if hist is None or hist.empty:
                continue
            close = hist["Close"].squeeze()
            if field in ("sp500_change", "dow_change", "nasdaq_change"):
                dfs[field] = close.pct_change() * 100
            else:
                dfs[field] = close
        except Exception as e:
            log.debug("Macro history failed for %s: %s", field, e)

    if not dfs:
        return pd.DataFrame()

    df = pd.DataFrame(dfs)
    df.index = pd.to_datetime(df.index).date
    df.index.name = "date"
    df = df.dropna(how="all")
    return df


def save_macro_to_lake(record: dict):
    """Upsert macro snapshot into raw_macro table."""
    from core.lake.manager import get_lake
    conn = get_lake()
    conn.execute("""
        INSERT OR REPLACE INTO raw_macro
            (date, crude_usd, usdinr, sp500_change, dow_change, nasdaq_change, india_vix, us_10y_yield)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        record["date"], record["crude_usd"], record["usdinr"],
        record["sp500_change"], record["dow_change"], record["nasdaq_change"],
        record["india_vix"], record["us_10y_yield"],
    ])
    conn.commit()


def save_macro_history_to_lake(df: pd.DataFrame):
    """Bulk-insert historical macro data into raw_macro."""
    from core.lake.manager import get_lake
    if df is None or df.empty:
        return
    conn = get_lake()
    df_reset = df.reset_index()
    conn.execute("""
        INSERT OR REPLACE INTO raw_macro
            (date, crude_usd, usdinr, sp500_change, dow_change, nasdaq_change, india_vix, us_10y_yield)
        SELECT
            date::DATE,
            crude_usd, usdinr, sp500_change, dow_change,
            nasdaq_change, india_vix, us_10y_yield
        FROM df_reset
    """)
    conn.commit()
    log.info("Saved %d macro history rows to lake", len(df))


def get_macro_regime(record: dict = None) -> dict:
    """
    Derive regime signals from current macro snapshot.
    Returns dict of regime flags useful as features.
    """
    if record is None:
        record = fetch_macro_snapshot()

    crude  = record.get("crude_usd") or 70
    usdinr = record.get("usdinr") or 83
    vix    = record.get("india_vix") or 15
    sp500  = record.get("sp500_change") or 0

    return {
        "crude_regime":   1 if crude > 90 else (-1 if crude < 70 else 0),
        "usdinr_regime":  1 if usdinr > 85 else (-1 if usdinr < 82 else 0),
        "vix_level":      2 if vix > 20 else (0 if vix < 15 else 1),  # 0=Low,1=Med,2=High
        "global_risk_on": sp500 > 0.5,   # US markets up = risk on
        "global_risk_off": sp500 < -0.5,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from core.lake.schema import init_schema
    init_schema()

    snap = fetch_macro_snapshot()
    print("Current macro:", json.dumps(snap, indent=2))
    save_macro_to_lake(snap)

    print("\nFetching 365-day history...")
    hist_df = fetch_macro_history(365)
    print(hist_df.tail())
    save_macro_history_to_lake(hist_df)
