from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from kairospy.core.views import ViewFieldSchema, ViewSchema

from ...model import RuntimeDataEnvelope


@dataclass(frozen=True, slots=True)
class SystemEventView:
    event_count: int = 0
    last_name: str | None = None
    last_stream: str | None = None
    last_event_time: datetime | None = None
    last_payload: dict[str, object] | None = None


class SystemEventProjection:
    key = "system.events"
    schema = ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("event_count", "consumed system event count", "runtime sequence", "system event"),
            ViewFieldSchema("last_name", "latest system event name", "event time", "system event"),
            ViewFieldSchema("last_stream", "latest system event stream", "event time", "system event"),
            ViewFieldSchema("last_event_time", "latest system event time", "event time", "system event"),
            ViewFieldSchema("last_payload", "latest system event payload", "event time", "system event"),
        ),
        mutability="runtime_writable",
        evidence="runtime system event projection",
    )

    def __init__(self) -> None:
        self._event_count = 0
        self._last_event: RuntimeDataEnvelope | None = None

    def on_event(self, event: RuntimeDataEnvelope) -> None:
        if event.domain == "system":
            self._event_count += 1
            self._last_event = event

    def view(self) -> SystemEventView:
        if self._last_event is None:
            return SystemEventView(event_count=self._event_count)
        return SystemEventView(
            event_count=self._event_count,
            last_name=self._last_event.kind,
            last_stream=self._last_event.stream,
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


__all__ = ["SystemEventProjection", "SystemEventView"]
