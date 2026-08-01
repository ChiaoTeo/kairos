from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.core.views import ViewFieldSchema, ViewSchema


class SystemViewKeys:
    strategy = "system.strategy"
    events = "system.events"


@dataclass(frozen=True, slots=True)
class StrategyLaunchView:
    strategy_id: str
    event_count: int = 0
    runtime_event_count: int = 0
    last_event_time: datetime | None = None
    last_domain: str | None = None
    last_kind: str | None = None
    status: str = "initialized"


@dataclass(frozen=True, slots=True)
class SystemEventView:
    event_count: int = 0
    last_name: str | None = None
    last_event_time: datetime | None = None
    last_payload: dict[str, object] | None = None


STRATEGY_LAUNCH_SCHEMA = ViewSchema(
    SystemViewKeys.strategy,
    "system",
    fields=(
        ViewFieldSchema("strategy_id", "strategy identity", "launch identity", "runtime"),
        ViewFieldSchema("event_count", "consumed runtime event count", "runtime sequence", "runtime"),
        ViewFieldSchema("runtime_event_count", "consumed runtime event count", "runtime sequence", "runtime"),
        ViewFieldSchema("last_event_time", "latest runtime event time", "event time", "runtime event source"),
        ViewFieldSchema("last_domain", "latest runtime event domain", "event time", "runtime event source"),
        ViewFieldSchema("last_kind", "latest runtime event kind", "event time", "runtime event source"),
        ViewFieldSchema("status", "runtime status", "runtime time", "runtime"),
    ),
    mutability="runtime_writable",
    evidence="strategy runtime loop view state",
)

SYSTEM_EVENTS_SCHEMA = ViewSchema(
    SystemViewKeys.events,
    "system",
    fields=(
        ViewFieldSchema("event_count", "consumed system event count", "runtime sequence", "system event"),
        ViewFieldSchema("last_name", "latest system event name", "event time", "system event"),
        ViewFieldSchema("last_event_time", "latest system event time", "event time", "system event"),
        ViewFieldSchema("last_payload", "latest system event payload", "event time", "system event"),
    ),
    mutability="runtime_writable",
    evidence="runtime system event view state",
)


__all__ = [
    "STRATEGY_LAUNCH_SCHEMA",
    "SYSTEM_EVENTS_SCHEMA",
    "StrategyLaunchView",
    "SystemEventView",
    "SystemViewKeys",
]
