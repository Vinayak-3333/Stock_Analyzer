"""Local and Kafka event bus adapters."""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict, deque
from typing import Callable

from .contracts import StockEvent
from .store import save_event

log = logging.getLogger("stockradar.events.bus")

EventHandler = Callable[[dict], None]


class LocalEventBus:
    """In-process event bus with DuckDB persistence."""

    def __init__(self, max_memory_events: int = 1000):
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._events: deque[dict] = deque(maxlen=max_memory_events)

    def subscribe(self, topic: str, handler: EventHandler) -> None:
        self._handlers[topic].append(handler)

    def publish(self, event: StockEvent | dict) -> dict:
        payload = event.to_dict() if isinstance(event, StockEvent) else event
        self._events.append(payload)
        save_event(payload)
        for handler in self._handlers.get(payload["topic"], []):
            try:
                handler(payload)
            except Exception as exc:
                log.warning("Event handler failed for %s: %s", payload["topic"], exc)
        return payload

    def recent(self, limit: int = 100) -> list[dict]:
        return list(self._events)[-limit:]


class KafkaEventBus(LocalEventBus):
    """Kafka producer with local persistence fallback."""

    def __init__(self, bootstrap_servers: str):
        super().__init__()
        self._producer = None
        self.bootstrap_servers = bootstrap_servers

    def _get_producer(self):
        if self._producer is None:
            from kafka import KafkaProducer

            self._producer = KafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
            )
        return self._producer

    def publish(self, event: StockEvent | dict) -> dict:
        payload = super().publish(event)
        try:
            producer = self._get_producer()
            producer.send(payload["topic"], payload)
            producer.flush(timeout=2)
        except Exception as exc:
            log.debug("Kafka publish skipped for %s: %s", payload["topic"], exc)
        return payload


_EVENT_BUS: LocalEventBus | None = None


def get_event_bus() -> LocalEventBus:
    global _EVENT_BUS
    if _EVENT_BUS is None:
        mode = os.getenv("STOCKRADAR_EVENT_BUS", "local").lower()
        if mode in {"kafka", "hybrid"}:
            bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
            _EVENT_BUS = KafkaEventBus(bootstrap)
        else:
            _EVENT_BUS = LocalEventBus()
    return _EVENT_BUS


def publish_event(event: StockEvent) -> dict:
    return get_event_bus().publish(event)
