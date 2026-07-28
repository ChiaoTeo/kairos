from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Protocol

from kairospy.core.views import ViewFieldSchema, ViewSchema


class MarketDataEnvelope(Protocol):
    domain: str
    stream: str
    sequence: int
    time: datetime
    payload: object


@dataclass(frozen=True, slots=True)
class MarketCurrentView:
    event_count: int = 0
    last_stream: str | None = None
    last_sequence: int | None = None
    last_event_time: datetime | None = None
    last_payload: dict[str, object] | None = None


class MarketCurrentProjection:
    key = "market.current"
    schema = ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("event_count", "consumed market event count", "runtime sequence", "event source"),
            ViewFieldSchema("last_stream", "latest market stream", "event time", "event source"),
            ViewFieldSchema("last_sequence", "latest market event sequence", "event order", "event source"),
            ViewFieldSchema("last_event_time", "latest market event time", "event time", "event source"),
            ViewFieldSchema("last_payload", "latest canonical market payload", "event time", "event source"),
        ),
        mutability="runtime_writable",
        evidence="market event projection",
    )

    def __init__(self) -> None:
        self._event_count = 0
        self._last_event: MarketDataEnvelope | None = None

    def on_event(self, event: MarketDataEnvelope) -> None:
        if event.domain == "market":
            self._event_count += 1
            self._last_event = event

    def view(self) -> MarketCurrentView:
        if self._last_event is None:
            return MarketCurrentView(event_count=self._event_count)
        return MarketCurrentView(
            event_count=self._event_count,
            last_stream=self._last_event.stream,
            last_sequence=self._last_event.sequence,
            last_event_time=self._last_event.time,
            last_payload=_payload_dict(self._last_event.payload),
        )


def _payload_dict(payload: object) -> dict[str, object]:
    if isinstance(payload, Mapping):
        return dict(payload)
    if hasattr(payload, "fields"):
        fields = getattr(payload, "fields")
        if isinstance(fields, Mapping):
            return dict(fields)
    return {"type": type(payload).__name__}


__all__ = ["MarketCurrentProjection", "MarketCurrentView"]
