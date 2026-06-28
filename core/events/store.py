"""Persistence helpers for event and score history."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from core.lake.manager import get_lake

log = logging.getLogger("stockradar.events.store")


def save_event(event: dict[str, Any]) -> None:
    try:
        conn = get_lake()
        conn.execute(
            """
            INSERT OR IGNORE INTO event_log
                (event_id, topic, stage, event_type, symbol, severity, source, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                event["event_id"],
                event["topic"],
                event["stage"],
                event["event_type"],
                event.get("symbol", "MARKET"),
                event.get("severity", "info"),
                event.get("source", "stockradar"),
                event.get("created_at"),
                json.dumps(event.get("payload") or {}, default=str),
            ],
        )
        conn.commit()
    except Exception as exc:
        log.debug("Event persistence failed: %s", exc)


def save_score_history(result: dict[str, Any], run_id: str | None = None) -> None:
    try:
        conn = get_lake()
        factor_scores = result.get("factor_scores") or {}
        conn.execute(
            """
            INSERT INTO score_history
                (run_id, symbol, scored_at, score, signal, confidence, price, volume,
                 factor_scores_json, reasons_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id,
                result.get("symbol"),
                datetime.utcnow().isoformat(),
                result.get("score"),
                result.get("signal"),
                result.get("confidence"),
                result.get("price"),
                result.get("live_volume"),
                json.dumps(factor_scores, default=str),
                json.dumps(result.get("top_reasons") or [], default=str),
            ],
        )
        conn.commit()
    except Exception as exc:
        log.debug("Score history persistence failed for %s: %s", result.get("symbol"), exc)


def get_previous_score(symbol: str) -> dict[str, Any] | None:
    try:
        conn = get_lake()
        row = conn.execute(
            """
            SELECT score, signal, scored_at
            FROM score_history
            WHERE symbol = ?
            ORDER BY scored_at DESC
            LIMIT 1
            """,
            [symbol],
        ).fetchone()
        if not row:
            return None
        return {"score": row[0], "signal": row[1], "scored_at": row[2]}
    except Exception as exc:
        log.debug("Previous score lookup failed for %s: %s", symbol, exc)
        return None


def get_recent_events(limit: int = 100, stage: str | None = None, symbol: str | None = None) -> list[dict[str, Any]]:
    try:
        limit = max(1, min(int(limit), 500))
        conn = get_lake()
        sql = """
            SELECT event_id, topic, stage, event_type, symbol, severity, source, created_at, payload_json
            FROM event_log
            WHERE 1 = 1
        """
        params: list[Any] = []
        if stage:
            sql += " AND stage = ?"
            params.append(stage)
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol.upper())
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        events = []
        for row in rows:
            events.append(
                {
                    "event_id": row[0],
                    "topic": row[1],
                    "stage": row[2],
                    "event_type": row[3],
                    "symbol": row[4],
                    "severity": row[5],
                    "source": row[6],
                    "created_at": row[7],
                    "payload": json.loads(row[8] or "{}"),
                }
            )
        return events
    except Exception as exc:
        log.debug("Recent events query failed: %s", exc)
        return []


def get_event_stats() -> dict[str, Any]:
    try:
        conn = get_lake()
        stage_rows = conn.execute(
            "SELECT stage, COUNT(*) FROM event_log GROUP BY stage ORDER BY stage"
        ).fetchall()
        latest = conn.execute("SELECT MAX(created_at) FROM event_log").fetchone()
        return {
            "events_by_stage": {row[0]: row[1] for row in stage_rows},
            "latest_event_at": latest[0] if latest else None,
        }
    except Exception as exc:
        log.debug("Event stats query failed: %s", exc)
        return {"events_by_stage": {}, "latest_event_at": None}
