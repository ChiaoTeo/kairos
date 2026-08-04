from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.domain.views import ViewFieldSchema, ViewSchema


class MonitorViewKeys:
    strategy = "system.strategy"
    events = "system.events"
    actors = "system.actors"
    supervisor = "system.supervisor"


@dataclass(frozen=True, slots=True)
class StrategyLaunchView:
    strategy_id: str
    event_count: int = 0
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


@dataclass(frozen=True, slots=True)
class ActorStatusView:
    actors: tuple[object, ...] = ()


@dataclass(frozen=True, slots=True)
class SupervisorStatusView:
    state: str = "initialized"
    at: datetime | None = None
    actors: tuple[str, ...] = ()


STRATEGY_LAUNCH_SCHEMA = ViewSchema(
    MonitorViewKeys.strategy,
    "system",
    fields=(
        ViewFieldSchema("strategy_id", "strategy identity", "launch identity", "runtime"),
        ViewFieldSchema("event_count", "consumed runtime event count", "runtime sequence", "runtime"),
        ViewFieldSchema("last_event_time", "latest runtime event time", "event time", "runtime event source"),
        ViewFieldSchema("last_domain", "latest runtime event domain", "event time", "runtime event source"),
        ViewFieldSchema("last_kind", "latest runtime event kind", "event time", "runtime event source"),
        ViewFieldSchema("status", "runtime status", "runtime time", "runtime"),
    ),
    mutability="runtime_writable",
    evidence="monitor Actor runtime loop view state",
)

SYSTEM_EVENTS_SCHEMA = ViewSchema(
    MonitorViewKeys.events,
    "system",
    fields=(
        ViewFieldSchema("event_count", "consumed system event count", "runtime sequence", "system event"),
        ViewFieldSchema("last_name", "latest system event name", "event time", "system event"),
        ViewFieldSchema("last_event_time", "latest system event time", "event time", "system event"),
        ViewFieldSchema("last_payload", "latest system event payload", "event time", "system event"),
    ),
    mutability="runtime_writable",
    evidence="monitor Actor system event view state",
)

ACTOR_STATUS_SCHEMA = ViewSchema(
    MonitorViewKeys.actors,
    "system",
    fields=(ViewFieldSchema("actors", "actor lifecycle states", "runtime state", "actor lifecycle event"),),
    mutability="runtime_writable",
    evidence="monitor Actor lifecycle state",
)

SUPERVISOR_STATUS_SCHEMA = ViewSchema(
    MonitorViewKeys.supervisor,
    "system",
    fields=(
        ViewFieldSchema("state", "supervisor lifecycle state", "runtime state", "supervisor lifecycle event"),
        ViewFieldSchema("at", "supervisor state time", "event time", "supervisor lifecycle event"),
        ViewFieldSchema("actors", "supervised actor names", "runtime state", "supervisor lifecycle event"),
    ),
    mutability="runtime_writable",
    evidence="monitor Supervisor lifecycle state",
)


__all__ = ["ACTOR_STATUS_SCHEMA", "ActorStatusView", "MonitorViewKeys", "STRATEGY_LAUNCH_SCHEMA", "SYSTEM_EVENTS_SCHEMA", "SUPERVISOR_STATUS_SCHEMA", "StrategyLaunchView", "SupervisorStatusView", "SystemEventView"]
