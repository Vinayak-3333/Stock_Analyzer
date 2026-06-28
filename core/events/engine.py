"""Rule event generation for the stage-8 real-time pipeline."""

from __future__ import annotations

from typing import Any

from .bus import publish_event
from .contracts import EventSeverity, EventType, PipelineStage, StockEvent
from .store import get_previous_score, save_score_history


def _confidence_from_result(result: dict[str, Any]) -> float:
    score = float(result.get("score") or 50)
    reasons = len(result.get("top_reasons") or [])
    volume_ratio = float(result.get("volume_ratio") or 1)
    score_strength = abs(score - 50) / 50
    confidence = 0.45 + score_strength * 0.35 + min(reasons, 5) * 0.03 + min(volume_ratio, 3) * 0.03
    return round(max(0.0, min(0.99, confidence)), 2)


def publish_symbol_stage_events(symbol: str, quote: dict[str, Any], features: dict[str, Any], result: dict[str, Any]) -> None:
    clean_symbol = symbol.replace(".NS", "")
    publish_event(
        StockEvent(
            stage=PipelineStage.INGESTION,
            event_type=EventType.DATA_INGESTED,
            symbol=clean_symbol,
            payload={
                "price": quote.get("lastPrice") or result.get("price"),
                "volume": quote.get("totalTradedVolume") or result.get("live_volume"),
                "source": "nse_yfinance",
            },
        )
    )
    publish_event(
        StockEvent(
            stage=PipelineStage.FEATURE_STORE,
            event_type=EventType.FEATURES_COMPUTED,
            symbol=clean_symbol,
            payload={
                "technical_score": features.get("technical_score"),
                "fundamental_score": features.get("fundamental_score"),
                "institutional_score": features.get("institutional_score"),
                "sentiment_score": features.get("sentiment_score"),
                "feature_count": len(features),
            },
        )
    )

    _publish_rule_triggers(clean_symbol, features, result)

    previous = get_previous_score(clean_symbol)
    confidence = _confidence_from_result(result)
    result["confidence"] = confidence
    publish_event(
        StockEvent(
            stage=PipelineStage.SCORING,
            event_type=EventType.SCORE_UPDATED,
            symbol=clean_symbol,
            severity=EventSeverity.ACTION if result.get("signal") in {"BUY", "SELL"} else EventSeverity.INFO,
            payload={
                "score": result.get("score"),
                "signal": result.get("signal"),
                "confidence": confidence,
                "factor_scores": result.get("factor_scores"),
                "top_reasons": result.get("top_reasons", []),
            },
        )
    )
    if previous and previous.get("score") is not None:
        delta = round(float(result.get("score") or 0) - float(previous["score"]), 2)
        if abs(delta) >= 5:
            publish_event(
                StockEvent(
                    stage=PipelineStage.SCORING,
                    event_type=EventType.SCORE_DELTA,
                    symbol=clean_symbol,
                    severity=EventSeverity.WATCH,
                    payload={
                        "previous_score": previous["score"],
                        "new_score": result.get("score"),
                        "delta": delta,
                        "previous_signal": previous.get("signal"),
                        "new_signal": result.get("signal"),
                    },
                )
            )

    publish_event(
        StockEvent(
            stage=PipelineStage.BACKTEST_LOG,
            event_type=EventType.BACKTEST_SNAPSHOT,
            symbol=clean_symbol,
            payload={
                "score": result.get("score"),
                "signal": result.get("signal"),
                "price": result.get("price"),
                "atr_pct": result.get("atr_pct"),
                "ready_for_replay": True,
            },
        )
    )
    publish_event(
        StockEvent(
            stage=PipelineStage.ML_LABELING,
            event_type=EventType.ML_LABEL_PENDING,
            symbol=clean_symbol,
            payload={"horizons": ["5d", "10d", "20d"], "label_method": "forward_return"},
        )
    )
    save_score_history(result)


def publish_risk_stage_events(results: list[dict[str, Any]]) -> None:
    for result in results:
        symbol = result.get("symbol") or "UNKNOWN"
        severity = EventSeverity.RISK if result.get("risk_flags") else EventSeverity.INFO
        publish_event(
            StockEvent(
                stage=PipelineStage.PORTFOLIO_RISK,
                event_type=EventType.RISK_UPDATED,
                symbol=symbol,
                severity=severity,
                payload={
                    "stop_loss": result.get("stop_loss"),
                    "target": result.get("target"),
                    "rr_ratio": result.get("rr_ratio"),
                    "position_size_pct": result.get("position_size_pct"),
                    "is_tradeable": result.get("is_tradeable"),
                    "risk_flags": result.get("risk_flags", []),
                },
            )
        )
        if float(result.get("position_size_pct") or 0) >= 10:
            publish_event(
                StockEvent(
                    stage=PipelineStage.PORTFOLIO_RISK,
                    event_type=EventType.PORTFOLIO_CONSTRAINT,
                    symbol=symbol,
                    severity=EventSeverity.RISK,
                    payload={"constraint": "max_single_stock_exposure", "limit_pct": 10},
                )
            )


def _publish_rule_triggers(symbol: str, features: dict[str, Any], result: dict[str, Any]) -> None:
    if features.get("t_breakout_52w"):
        publish_event(
            StockEvent(
                stage=PipelineStage.EVENT_ENGINE,
                event_type=EventType.TECHNICAL_TRIGGER,
                symbol=symbol,
                severity=EventSeverity.ACTION,
                payload={"rule": "52w_breakout", "price": result.get("price"), "volume_ratio": result.get("volume_ratio")},
            )
        )
    if float(features.get("t_volume_ratio") or 0) >= 2:
        publish_event(
            StockEvent(
                stage=PipelineStage.EVENT_ENGINE,
                event_type=EventType.VOLUME_TRIGGER,
                symbol=symbol,
                severity=EventSeverity.WATCH,
                payload={"rule": "volume_ratio_ge_2", "volume_ratio": features.get("t_volume_ratio")},
            )
        )
    if str(features.get("s_event_type") or "none").lower() not in {"", "none"}:
        publish_event(
            StockEvent(
                stage=PipelineStage.EVENT_ENGINE,
                event_type=EventType.NEWS_TRIGGER,
                symbol=symbol,
                severity=EventSeverity.WATCH,
                payload={"event_type": features.get("s_event_type"), "sentiment": features.get("s_sentiment_label")},
            )
        )
    if bool(features.get("i_delivery_spike")) or float(features.get("i_fii_net_3d") or 0) > 1000:
        publish_event(
            StockEvent(
                stage=PipelineStage.EVENT_ENGINE,
                event_type=EventType.INSTITUTIONAL_TRIGGER,
                symbol=symbol,
                severity=EventSeverity.WATCH,
                payload={
                    "delivery_spike": features.get("i_delivery_spike"),
                    "fii_net_3d": features.get("i_fii_net_3d"),
                },
            )
        )
