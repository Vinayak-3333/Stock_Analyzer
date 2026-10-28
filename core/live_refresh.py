"""
Intraday live refresh — re-scores the last deep run's stocks on live data.

A deep run (pipeline.run_modular_analysis) takes 8–30 minutes and is bound
by Yahoo / Google News / NSE rate limits, so it can only run a few times a
day. In between, this refresh keeps the dashboard live every few minutes
for the price of ~30 NSE calls and no Yahoo calls per stock:

  * live price, change and volume for every stock (NSE index + traded feeds)
  * today's live bar spliced into each stock's history, so every technical
    indicator — and the technical factor score — is recomputed as of now
  * the live market regime (NIFTY / VIX / sectors from NSE allIndices)
  * final score and signal recombined from the deep run's other factors
    (fundamental, institutional, sentiment, sector, risk), which do not move
    intraday, plus stop-loss / target from the risk engine at the live price

Histories come from the deep run's in-memory copy, or the lake's raw_ohlcv
after a restart. Nothing here writes to `runs` or raw_ohlcv: the run history
and the forward-return labels only ever see completed deep runs.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import pandas as pd

from core import pipeline as p
from core.collectors.nse import collect_all_quotes, fetch_live_indices, fetch_traded_quotes
from core.features.live_bar import parse_nse_time, quote_from_index_row, with_live_bar
from core.features.regime import compute_regime_features
from core.features.technical import compute_technical_features
from core.lake.manager import close_lake, get_lake
from core.risk.engine import apply_risk_to_results
from core.scoring.hybrid import combine_factor_scores, technical_score

log = logging.getLogger("stockradar.live_refresh")

_HISTORY_ROWS = 260  # ~1 year of sessions, what the deep run screens on

# ^NSEI daily history for relative strength; it only gains a row a day.
_nifty_cache: dict = {"at": 0.0, "df": None}
_NIFTY_TTL_SECONDS = 3600


def _nifty_history() -> Optional[pd.DataFrame]:
    if _nifty_cache["df"] is None or time.time() - _nifty_cache["at"] > _NIFTY_TTL_SECONDS:
        _nifty_cache["df"] = p._download_history("^NSEI")
        _nifty_cache["at"] = time.time()
    return _nifty_cache["df"]


def _lake_histories(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Completed daily bars from raw_ohlcv, for symbols not held in memory."""
    if not symbols:
        return {}
    try:
        rows = get_lake().execute(
            """
            SELECT symbol, date, open, high, low, close, volume FROM (
                SELECT *, row_number() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
                FROM raw_ohlcv WHERE symbol IN (SELECT unnest(?))
            ) WHERE rn <= ? ORDER BY symbol, date
            """,
            [symbols, _HISTORY_ROWS],
        ).fetchdf()
    except Exception as exc:
        log.warning("Live refresh: lake history read failed: %s", exc)
        return {}
    out = {}
    for sym, g in rows.groupby("symbol"):
        df = g.set_index(pd.DatetimeIndex(pd.to_datetime(g["date"])))[
            ["open", "high", "low", "close", "volume"]
        ].rename(columns=str.capitalize)
        if len(df) >= p.MIN_HISTORY_ROWS:
            out[sym] = df.astype("float64")
    return out


def refresh_results(results: list[dict]) -> tuple[list[dict], dict, Optional[str]]:
    """
    Re-score `results` (a deep run's result rows) on live data.

    Returns (refreshed_results, market_payload, as_of) — as_of is NSE's
    timestamp for the quotes, ISO, or None if NSE gave no live quotes, in
    which case the rows come back with only the regime re-applied.
    """
    started = time.monotonic()
    session = p._make_session()
    quotes = collect_all_quotes(session)
    traded = fetch_traded_quotes(session)
    for sym, q in traded.items():
        quotes.setdefault(sym, q)  # index rows are richer (OHLC, 52w); keep them

    regime = compute_regime_features()
    market = p._market_payload(regime)
    regime_code = p._regime_code(regime)
    nifty_df = with_live_bar(
        _nifty_history(), quote_from_index_row(fetch_live_indices(session).get("NIFTY 50"))
    )

    symbols = [r["symbol"] for r in results]
    histories = {s: p.DEEP_RUN_HISTORIES[s] for s in symbols if s in p.DEEP_RUN_HISTORIES}
    try:
        histories.update(_lake_histories([s for s in symbols if s not in histories]))
    finally:
        close_lake()

    refreshed, live_count = [], 0
    for row in results:
        sym = row["symbol"]
        quote = quotes.get(sym)
        new = dict(row)
        factors = dict(row.get("factor_scores") or {})
        df = with_live_bar(histories.get(sym), quote)
        if quote and df is not None and df.attrs.get("live_bar"):
            try:
                tech = {f"t_{k}": v for k, v in compute_technical_features(df, nifty_df).items()}
                factors["technical"] = round(technical_score(p._technical_inputs(tech))[0], 1)
                new.update(p._price_and_technical_fields(tech, quote))
                live_count += 1
            except Exception as exc:
                log.debug("Live refresh: technicals failed for %s: %s", sym, exc)
        final, signal, multiplier = combine_factor_scores(
            factors, regime=regime_code, disqualified=bool(row.get("is_disqualified")),
        )
        new.update(score=final, signal=signal, factor_scores=factors, regime_multiplier=multiplier)
        refreshed.append(new)

    refreshed = apply_risk_to_results(refreshed)
    as_of_values = [parse_nse_time(q.get("as_of")) for q in quotes.values()]
    as_of_values = [t for t in as_of_values if t]
    as_of = max(as_of_values).isoformat(timespec="seconds") if as_of_values else None
    log.info(
        "Live refresh: %d/%d stocks re-scored on live bars (as of %s, %.0fs; %d histories from memory)",
        live_count, len(results), as_of, time.monotonic() - started,
        sum(1 for s in symbols if s in p.DEEP_RUN_HISTORIES),
    )
    return refreshed, market, as_of
