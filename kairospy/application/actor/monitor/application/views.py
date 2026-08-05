from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.domain.views import ViewFieldSchema, ViewSchema


class MonitorViewKeys:
    strategy = "system.strategy"
    events = "system.events"
    actors = "system.actors"
    supervisor = "system.supervisor"
    health = "system.health"
    operations = "system.operations"
    freshness = "system.freshness"
    alerts = "system.alerts"


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


@dataclass(frozen=True, slots=True)
class ActorHealthView:
    actor: str
    state: str = "initialized"
    started: bool = False
    stopping: bool = False
    processed_count: int = 0
    error_count: int = 0
    mailbox_depth: int = 0
    event_loops: int = 0
    last_processed_at: datetime | None = None
    last_error: str | None = None
    last_event_time: datetime | None = None


@dataclass(frozen=True, slots=True)
class ConnectionHealthView:
    connection: str
    status: str = "unknown"
    healthy: bool | None = None
    authenticated: bool | None = None
    reconnect_count: int = 0
    last_event_time: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class SystemHealthView:
    status: str = "initialized"
    mode: str | None = None
    event_count: int = 0
    error_count: int = 0
    last_event_time: datetime | None = None
    last_processed_at: datetime | None = None
    actors: tuple[ActorHealthView, ...] = ()
    connections: tuple[ConnectionHealthView, ...] = ()
    stale: bool = False


@dataclass(frozen=True, slots=True)
class OperationMonitorView:
    operation_id: str
    stage: str
    status: str = "active"
    intent_id: str | None = None
    reservation_id: str | None = None
    order_id: str | None = None
    account_id: str | None = None
    first_event_time: datetime | None = None
    last_event_time: datetime | None = None
    last_topic: str | None = None
    error: str | None = None
    stale: bool = False
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class OperationCollectionView:
    operations: tuple[OperationMonitorView, ...] = ()


@dataclass(frozen=True, slots=True)
class AlertView:
    alert_id: str
    rule: str
    severity: str
    status: str
    first_seen: datetime
    last_seen: datetime
    resolved_at: datetime | None = None
    correlation_id: str | None = None
    details: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class AlertCollectionView:
    alerts: tuple[AlertView, ...] = ()


@dataclass(frozen=True, slots=True)
class FreshnessView:
    last_event_time: datetime | None = None
    last_market_event_time: datetime | None = None
    last_account_event_time: datetime | None = None
    last_execution_event_time: datetime | None = None
    last_risk_event_time: datetime | None = None
    last_system_event_time: datetime | None = None
    stale_domains: tuple[str, ...] = ()


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

HEALTH_SCHEMA = ViewSchema(
    MonitorViewKeys.health,
    "system",
    fields=(
        ViewFieldSchema("status", "overall runtime health", "runtime state", "monitor observation"),
        ViewFieldSchema("mode", "launch execution mode", "runtime state", "launch configuration"),
        ViewFieldSchema("event_count", "observed event count", "runtime sequence", "message envelope"),
        ViewFieldSchema("error_count", "observed actor error count", "runtime state", "actor metric"),
        ViewFieldSchema("last_event_time", "latest observed event time", "event time", "message envelope"),
        ViewFieldSchema("last_processed_at", "latest actor processing time", "event time", "actor metric"),
        ViewFieldSchema("actors", "actor health snapshots", "runtime state", "actor metric"),
        ViewFieldSchema("connections", "connection health snapshots", "runtime state", "connection health"),
        ViewFieldSchema("stale", "whether runtime health is stale", "runtime state", "monitor observation"),
    ),
    mutability="runtime_writable",
    evidence="monitor Actor runtime health projection",
)

OPERATIONS_SCHEMA = ViewSchema(
    MonitorViewKeys.operations,
    "system",
    fields=(ViewFieldSchema("operations", "intent to account operation chains", "event time", "business events"),),
    mutability="runtime_writable",
    evidence="monitor Actor business operation projection",
)

FRESHNESS_SCHEMA = ViewSchema(
    MonitorViewKeys.freshness,
    "system",
    fields=(
        ViewFieldSchema("last_event_time", "latest observed event time", "event time", "message envelope"),
        ViewFieldSchema("last_market_event_time", "latest market event time", "event time", "market event"),
        ViewFieldSchema("last_account_event_time", "latest account event time", "event time", "account event"),
        ViewFieldSchema("last_execution_event_time", "latest execution event time", "event time", "execution event"),
        ViewFieldSchema("last_risk_event_time", "latest risk event time", "event time", "risk event"),
        ViewFieldSchema("last_system_event_time", "latest system event time", "event time", "system event"),
        ViewFieldSchema("stale_domains", "domains without recent events", "runtime state", "monitor observation"),
    ),
    mutability="runtime_writable",
    evidence="monitor Actor event freshness projection",
)

ALERTS_SCHEMA = ViewSchema(
    MonitorViewKeys.alerts,
    "system",
    fields=(ViewFieldSchema("alerts", "active and resolved monitor alerts", "event time", "monitor rule"),),
    mutability="runtime_writable",
    evidence="monitor Actor alert projection",
)


__all__ = ["ACTOR_STATUS_SCHEMA", "ALERTS_SCHEMA", "AlertCollectionView", "AlertView", "ActorHealthView", "ActorStatusView", "ConnectionHealthView", "FRESHNESS_SCHEMA", "FreshnessView", "HEALTH_SCHEMA", "MonitorViewKeys", "OPERATIONS_SCHEMA", "OperationCollectionView", "OperationMonitorView", "STRATEGY_LAUNCH_SCHEMA", "SYSTEM_EVENTS_SCHEMA", "SUPERVISOR_STATUS_SCHEMA", "StrategyLaunchView", "SupervisorStatusView", "SystemEventView", "SystemHealthView"]
