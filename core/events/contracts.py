"""Typed event contracts for the real-time StockRadar pipeline."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class PipelineStage(str, Enum):
    INGESTION = "stage_1_ingestion"
    FEATURE_STORE = "stage_2_feature_store"
    EVENT_ENGINE = "stage_3_event_engine"
    SCORING = "stage_4_scoring"
    BACKTEST_LOG = "stage_5_backtest_log"
    ML_LABELING = "stage_6_ml_labeling"
    DEPLOYMENT = "stage_7_deployment"
    PORTFOLIO_RISK = "stage_8_portfolio_risk"


class EventType(str, Enum):
    PIPELINE_STARTED = "pipeline.started"
    PIPELINE_COMPLETED = "pipeline.completed"
    DATA_INGESTED = "data.ingested"
    FEATURES_COMPUTED = "features.computed"
    TECHNICAL_TRIGGER = "trigger.technical"
    VOLUME_TRIGGER = "trigger.volume"
    NEWS_TRIGGER = "trigger.news"
    INSTITUTIONAL_TRIGGER = "trigger.institutional"
    SCORE_UPDATED = "score.updated"
    SCORE_DELTA = "score.delta"
    BACKTEST_SNAPSHOT = "backtest.snapshot"
    ML_LABEL_PENDING = "ml.label_pending"
    API_READY = "deployment.api_ready"
    RISK_UPDATED = "risk.updated"
    PORTFOLIO_CONSTRAINT = "portfolio.constraint"
    ALERT_CANDIDATE = "alert.candidate"
    ERROR = "pipeline.error"


class EventSeverity(str, Enum):
    INFO = "info"
    WATCH = "watch"
    ACTION = "action"
    RISK = "risk"
    ERROR = "error"


TOPIC_BY_STAGE = {
    PipelineStage.INGESTION: "stockradar.ingestion",
    PipelineStage.FEATURE_STORE: "stockradar.features",
    PipelineStage.EVENT_ENGINE: "stockradar.events",
    PipelineStage.SCORING: "stockradar.scores",
    PipelineStage.BACKTEST_LOG: "stockradar.backtest",
    PipelineStage.ML_LABELING: "stockradar.ml",
    PipelineStage.DEPLOYMENT: "stockradar.deployment",
    PipelineStage.PORTFOLIO_RISK: "stockradar.risk",
}


@dataclass(slots=True)
class StockEvent:
    stage: PipelineStage
    event_type: EventType
    symbol: str = "MARKET"
    payload: dict[str, Any] = field(default_factory=dict)
    severity: EventSeverity = EventSeverity.INFO
    topic: str | None = None
    source: str = "stockradar"
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        topic = self.topic or TOPIC_BY_STAGE[self.stage]
        return {
            "event_id": self.event_id,
            "topic": topic,
            "stage": self.stage.value,
            "event_type": self.event_type.value,
            "symbol": self.symbol,
            "severity": self.severity.value,
            "source": self.source,
            "created_at": self.created_at,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StockEvent":
        return cls(
            event_id=value.get("event_id") or str(uuid.uuid4()),
            topic=value.get("topic"),
            stage=PipelineStage(value["stage"]),
            event_type=EventType(value["event_type"]),
            symbol=value.get("symbol") or "MARKET",
            severity=EventSeverity(value.get("severity") or EventSeverity.INFO),
            source=value.get("source") or "stockradar",
            created_at=value.get("created_at") or datetime.now(timezone.utc).isoformat(),
            payload=value.get("payload") or {},
        )
