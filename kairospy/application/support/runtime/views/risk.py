from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.core.views import ViewFieldSchema, ViewSchema


class RiskViewKeys:
    events = "risk.events"


@dataclass(frozen=True, slots=True)
class RiskEventView:
    event_count: int = 0
    last_name: str | None = None
    last_event_time: datetime | None = None
    last_payload: dict[str, object] | None = None


RISK_EVENTS_SCHEMA = ViewSchema(
    RiskViewKeys.events,
    "system",
    fields=(
        ViewFieldSchema("event_count", "consumed risk event count", "runtime sequence", "risk event"),
        ViewFieldSchema("last_name", "latest risk event name", "event time", "risk event"),
        ViewFieldSchema("last_event_time", "latest risk event time", "event time", "risk event"),
        ViewFieldSchema("last_payload", "latest risk event payload", "event time", "risk event"),
    ),
    mutability="runtime_writable",
    evidence="runtime risk event view state",
)


__all__ = ["RISK_EVENTS_SCHEMA", "RiskEventView", "RiskViewKeys"]
