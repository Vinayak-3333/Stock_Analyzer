"""
Today's live bar on top of a daily history.

Every indicator in the pipeline is computed on Yahoo daily bars. During the
session those end at yesterday's close, or at a partial today row that is
~15 min delayed. `with_live_bar` replaces or appends today's row from NSE's
live quote, so indicators keep their exact meaning (RSI-14 is still 14 daily
bars) but their last bar is "now".

The bar's date comes from NSE's own timestamp, not the wall clock: after the
close or on a holiday NSE still reports the last session, and that session's
row is what gets refreshed — never a phantom row dated today.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

_NSE_TIME_FORMATS = (
    "%d-%b-%Y %H:%M:%S",   # equity-stock-indices / stocksTraded top level
    "%d-%b-%Y %H:%M",      # allIndices
    "%Y-%m-%d %H:%M:%S",   # per-row lastUpdateTime
)


def parse_nse_time(value: Optional[str]) -> Optional[datetime]:
    """NSE timestamps (IST, naive) in any of the formats its APIs use."""
    if not value:
        return None
    for fmt in _NSE_TIME_FORMATS:
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except ValueError:
            continue
    return None


def quote_from_index_row(row: Optional[dict]) -> Optional[dict]:
    """An `allIndices` row in the pipeline's quote shape, for with_live_bar."""
    if not row:
        return None
    return {
        "lastPrice": row.get("last"),
        "open": row.get("open"),
        "dayHigh": row.get("high"),
        "dayLow": row.get("low"),
        "previousClose": row.get("previousClose"),
        "pChange": row.get("percentChange"),
        "as_of": row.get("as_of"),
    }


def _num(value) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f and f > 0 else None  # drop NaN, 0 and "-"


def with_live_bar(df: Optional[pd.DataFrame], quote: Optional[dict]) -> Optional[pd.DataFrame]:
    """
    Return `df` with the row for the quote's session set from the live quote.

    `quote` uses the pipeline's quote keys: lastPrice (required), open,
    dayHigh, dayLow, totalTradedVolume, and as_of (NSE timestamp, required).
    Fields NSE didn't supply keep the history's values; high/low are always
    widened to include the live price. A quote older than the history's last
    row, or without a price or timestamp, leaves `df` unchanged.
    """
    if df is None or df.empty or not quote:
        return df
    last = _num(quote.get("lastPrice"))
    as_of = parse_nse_time(quote.get("as_of"))
    if last is None or as_of is None or not isinstance(df.index, pd.DatetimeIndex):
        return df

    bar_day = pd.Timestamp(as_of.date())
    index_tz = df.index.tz
    if index_tz is not None:
        bar_day = bar_day.tz_localize(index_tz)
    last_day = df.index[-1].normalize()
    if bar_day < last_day:
        return df

    out = df.copy()
    if bar_day == last_day:
        row = out.iloc[-1].copy()
        label = out.index[-1]
    else:
        row = pd.Series(index=out.columns, dtype="float64")
        label = bar_day

    def _set(col, value):
        if col in out.columns and value is not None:
            row[col] = value

    open_ = _num(quote.get("open"))
    _set("Open", open_ if open_ is not None else (row.get("Open") if pd.notna(row.get("Open")) else last))
    high = max(v for v in (_num(quote.get("dayHigh")), _num(row.get("High")), last) if v is not None)
    low = min(v for v in (_num(quote.get("dayLow")), _num(row.get("Low")), last) if v is not None)
    _set("High", high)
    _set("Low", low)
    _set("Close", last)
    volume = _num(quote.get("totalTradedVolume"))
    if volume is not None:
        _set("Volume", volume)

    out.loc[label] = row
    # The last row is an unfinished session: callers that persist daily bars
    # (pipeline._store_ohlcv) must leave it out.
    out.attrs["live_bar"] = True
    return out
