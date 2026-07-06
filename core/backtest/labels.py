"""
Forward-Return Labels + Factor IC Report
=========================================
Closes the scoring feedback loop:

1. ``backfill_labels()`` — fills ``label_5d_return`` / ``label_10d_return`` /
   ``label_20d_return`` on ``score_history`` rows by looking up the close
   N *trading days* after each score was recorded (daily bars from
   ``raw_bhavcopy`` ∪ ``raw_ohlcv``).  Idempotent and incremental: already
   -filled labels are never overwritten, and rows are retried each run
   until the 20-day label is available.

2. ``compute_factor_ic()`` — measures, per horizon, the Spearman rank
   information coefficient of each factor sub-score (and the composite
   score) against realised forward returns, plus per-signal hit rates.
   This is the evidence for whether the hand-set WEIGHTS deserve their
   values.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import pandas as pd

log = logging.getLogger("stockradar.labels")

HORIZONS = (5, 10, 20)
FACTOR_KEYS = ("fundamental", "technical", "institutional", "sentiment", "sector", "risk")

# Daily close per symbol from both lake sources. Bhavcopy covers the whole
# EQ universe daily; raw_ohlcv covers whatever the pipeline analysed.
_PRICE_SOURCE_SQL = """
    SELECT symbol, date, MAX(close) AS close
    FROM (
        SELECT symbol, date, close FROM raw_bhavcopy WHERE close IS NOT NULL AND close > 0
        UNION ALL
        SELECT symbol, date, close FROM raw_ohlcv   WHERE close IS NOT NULL AND close > 0
    )
    GROUP BY symbol, date
"""

_PENDING_SQL = """
    SELECT count(*) FROM score_history
    WHERE label_20d_return IS NULL AND price IS NOT NULL AND price > 0
"""


def backfill_labels() -> int:
    """
    Fill forward-return labels on score_history. Returns the number of rows
    that received at least one new label value.
    """
    from core.lake.manager import get_lake
    conn = get_lake()

    pending_before = conn.execute(_PENDING_SQL).fetchone()[0]
    if not pending_before:
        return 0

    # rn = Nth trading day after the scoring date (per score row).
    conn.execute(f"""
        UPDATE score_history SET
            label_5d_return  = COALESCE(label_5d_return,  p.r5),
            label_10d_return = COALESCE(label_10d_return, p.r10),
            label_20d_return = COALESCE(label_20d_return, p.r20)
        FROM (
            WITH px AS ({_PRICE_SOURCE_SQL}),
            future AS (
                SELECT s.id AS sid, s.price AS entry, px.close AS fclose,
                       ROW_NUMBER() OVER (PARTITION BY s.id ORDER BY px.date) AS rn
                FROM score_history s
                JOIN px ON px.symbol = s.symbol
                       AND px.date > CAST(s.scored_at AS DATE)
                WHERE s.label_20d_return IS NULL
                  AND s.price IS NOT NULL AND s.price > 0
            )
            SELECT sid,
                   ROUND((MAX(CASE WHEN rn = 5  THEN fclose END) / MAX(entry) - 1) * 100, 3) AS r5,
                   ROUND((MAX(CASE WHEN rn = 10 THEN fclose END) / MAX(entry) - 1) * 100, 3) AS r10,
                   ROUND((MAX(CASE WHEN rn = 20 THEN fclose END) / MAX(entry) - 1) * 100, 3) AS r20
            FROM future
            WHERE rn <= 20
            GROUP BY sid
        ) p
        WHERE score_history.id = p.sid
          AND (p.r5 IS NOT NULL OR p.r10 IS NOT NULL OR p.r20 IS NOT NULL)
    """)
    conn.commit()

    # "Filled" here = rows whose 5d label appeared; cheap proxy for progress
    filled_5d = conn.execute(
        "SELECT count(*) FROM score_history WHERE label_5d_return IS NOT NULL"
    ).fetchone()[0]
    pending_after = conn.execute(_PENDING_SQL).fetchone()[0]
    log.info(
        "Label backfill: %d rows still awaiting 20d labels (was %d); %d rows have 5d labels",
        pending_after, pending_before, filled_5d,
    )
    return int(pending_before - pending_after)


def _spearman(a: pd.Series, b: pd.Series) -> float | None:
    """Spearman rank correlation without a scipy dependency."""
    mask = a.notna() & b.notna()
    if mask.sum() < 3 or a[mask].nunique() < 2 or b[mask].nunique() < 2:
        return None
    return float(a[mask].rank().corr(b[mask].rank()))


def compute_factor_ic(days: int = 90, min_sample: int = 30) -> dict[str, Any]:
    """
    Factor information-coefficient report over the last *days* of labelled
    score history.

    For each horizon (5/10/20 trading days):
      * ``ic``          — Spearman rank correlation of each factor sub-score
                          (and the composite score) with the forward return.
      * ``signals``     — per-signal sample size, mean forward return,
                          hit rate (% positive) and excess vs the universe.
    """
    from core.lake.manager import get_lake
    conn = get_lake()

    df = conn.execute(
        """
        SELECT symbol, scored_at, score, signal, factor_scores_json,
               label_5d_return, label_10d_return, label_20d_return
        FROM score_history
        WHERE scored_at >= CAST(now() AS TIMESTAMP) - ? * INTERVAL 1 DAY
          AND label_5d_return IS NOT NULL
        """,
        [int(days)],
    ).df()

    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "window_days": int(days),
        "labelled_rows": int(len(df)),
        "min_sample": int(min_sample),
        "horizons": {},
    }
    if df.empty:
        report["note"] = (
            "No labelled rows yet — labels need at least 5 trading days of "
            "bhavcopy history after a scored run. Keep the scheduler running."
        )
        return report

    # Expand factor_scores_json → one numeric column per factor
    parsed = df["factor_scores_json"].map(lambda s: json.loads(s) if s else {})
    for key in FACTOR_KEYS:
        df[key] = pd.to_numeric(parsed.map(lambda d, k=key: d.get(k)), errors="coerce")
    df["score"] = pd.to_numeric(df["score"], errors="coerce")

    for h in HORIZONS:
        col = f"label_{h}d_return"
        sub = df[pd.to_numeric(df[col], errors="coerce").notna()].copy()
        if sub.empty:
            continue
        sub[col] = pd.to_numeric(sub[col], errors="coerce")

        ics: dict[str, Any] = {}
        for key in (*FACTOR_KEYS, "score"):
            ic = _spearman(sub[key], sub[col])
            if ic is not None:
                ics["composite" if key == "score" else key] = round(ic, 4)

        universe_mean = float(sub[col].mean())
        signals: dict[str, Any] = {}
        for sig, grp in sub.groupby("signal"):
            rets = grp[col]
            signals[str(sig)] = {
                "n": int(len(grp)),
                "avg_return_pct": round(float(rets.mean()), 3),
                "hit_rate_pct": round(float((rets > 0).mean() * 100), 1),
                "excess_vs_universe_pct": round(float(rets.mean()) - universe_mean, 3),
            }

        report["horizons"][f"{h}d"] = {
            "n": int(len(sub)),
            "reliable": bool(len(sub) >= min_sample),
            "universe_avg_return_pct": round(universe_mean, 3),
            "ic": ics,
            "signals": signals,
        }

    return report
