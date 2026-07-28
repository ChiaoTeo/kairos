from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.views import ViewFieldSchema, ViewSchema


@dataclass(frozen=True, slots=True)
class RiskEventView:
    event_count: int = 0
    last_name: str | None = None
    last_event_time: datetime | None = None
    last_payload: dict[str, object] | None = None


class RiskEventProjection:
    key = "risk.events"
    schema = ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("event_count", "consumed risk event count", "runtime sequence", "risk event"),
            ViewFieldSchema("last_name", "latest risk event name", "event time", "risk event"),
            ViewFieldSchema("last_event_time", "latest risk event time", "event time", "risk event"),
            ViewFieldSchema("last_payload", "latest risk event payload", "event time", "risk event"),
        ),
        mutability="runtime_writable",
        evidence="runtime risk event projection",
    )

    def __init__(self) -> None:
        self._event_count = 0
        self._last_event: RuntimeEnvelope | None = None

    def on_event(self, event: RuntimeEnvelope) -> None:
        if event.domain == "system" and event.kind.startswith("risk."):
            self._event_count += 1
            self._last_event = event

    def view(self) -> RiskEventView:
        if self._last_event is None:
            return RiskEventView(event_count=self._event_count)
        return RiskEventView(
            event_count=self._event_count,
            last_name=self._last_event.kind,
            last_event_time=self._last_event.time,
            last_payload=_payload_dict(self._last_event.payload),
        )


def _payload_dict(payload: object) -> dict[str, object]:
    if isinstance(payload, Mapping):
        return dict(payload)
    return {"type": type(payload).__name__}


__all__ = ["RiskEventProjection", "RiskEventView"]
