from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping

from kairospy.application.support.messaging import Message
from kairospy.application.support.runtime.application.views import ViewStore
from .views import (
    ACTOR_STATUS_SCHEMA,
    ALERTS_SCHEMA,
    AlertCollectionView,
    AlertView,
    ActorHealthView,
    ActorStatusView,
    ConnectionHealthView,
    FRESHNESS_SCHEMA,
    FreshnessView,
    HEALTH_SCHEMA,
    STRATEGY_LAUNCH_SCHEMA,
    SYSTEM_EVENTS_SCHEMA,
    SUPERVISOR_STATUS_SCHEMA,
    StrategyLaunchView,
    SupervisorStatusView,
    SystemEventView,
    SystemHealthView,
    OperationMonitorView,
    OperationCollectionView,
    OPERATIONS_SCHEMA,
    MonitorViewKeys,
)


class MonitorActorProjectors:
    def __init__(self, *, strategy_id: str, actor_states: object, supervisor_state: object, actor_sources: object | None = None, connections: object | None = None, freshness_stale_after: timedelta | None = timedelta(minutes=5)) -> None:
        self._actor_states = actor_states
        self._supervisor_state = supervisor_state
        self._actor_sources = actor_sources or (lambda: ())
        self._connections = connections or (lambda: ())
        self.runtime = _RuntimeState(strategy_id)
        self.events = _SystemEvents()
        self.health = _HealthState(self._actor_states, self._actor_sources, self._connections)
        self.freshness = _FreshnessState(freshness_stale_after)
        self.operations = _OperationsState()
        self.alerts = _AlertsState(self.freshness, self.operations, freshness_stale_after)
        self._states = (self.runtime, self.events, self.health, self.freshness, self.operations, self.alerts)

    def on_event(self, event: Message) -> None:
        for state in self._states:
            state.on_event(event)

    def register_views(self, views: ViewStore) -> None:
        for state in self._states:
            if views.registry.get(state.schema.key) is None:
                views.register(state.schema)
        for schema in (ACTOR_STATUS_SCHEMA, SUPERVISOR_STATUS_SCHEMA):
            if views.registry.get(schema.key) is None:
                views.register(schema)
        for state in (self.health, self.operations, self.freshness):
            if views.registry.get(state.schema.key) is None:
                views.register(state.schema)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        for state in self._states:
            views.put_runtime(state.key, state.view(), as_of=as_of, available_time=as_of)
        views.put_runtime(
            MonitorViewKeys.actors,
            ActorStatusView(tuple(self._actor_states())),
            as_of=as_of,
            available_time=as_of,
        )
        supervisor = self._supervisor_state()
        views.put_runtime(
            MonitorViewKeys.supervisor,
            SupervisorStatusView(
                state="initialized" if supervisor is None else supervisor.state,
                at=None if supervisor is None else supervisor.at,
                actors=() if supervisor is None else supervisor.actors,
            ),
            as_of=as_of,
            available_time=as_of,
        )
        views.put_runtime(MonitorViewKeys.health, self.health.view(), as_of=as_of, available_time=as_of)
        views.put_runtime(MonitorViewKeys.operations, self.operations.view(), as_of=as_of, available_time=as_of)
        views.put_runtime(MonitorViewKeys.freshness, self.freshness.view(as_of=as_of), as_of=as_of, available_time=as_of)
        views.put_runtime(MonitorViewKeys.alerts, self.alerts.view(), as_of=as_of, available_time=as_of)

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        self.operations.on_intents(intents, context, hook)

    def reconcile_alerts(self, as_of: datetime) -> tuple[AlertView, ...]:
        return self.alerts.reconcile(as_of)

    def drain_alert_facts(self) -> tuple[tuple[str, AlertView], ...]:
        return self.alerts.drain_facts()


class _RuntimeState:
    key = MonitorViewKeys.strategy
    schema = STRATEGY_LAUNCH_SCHEMA

    def __init__(self, strategy_id: str) -> None:
        self.strategy_id = strategy_id
        self._event_count = 0
        self._last_event: Message | None = None
        self.status = "initialized"

    def on_event(self, event: Message) -> None:
        self._event_count += 1
        self._last_event = event
        self.status = "running"

    def view(self) -> StrategyLaunchView:
        return StrategyLaunchView(
            strategy_id=self.strategy_id,
            event_count=self._event_count,
            last_event_time=None if self._last_event is None else self._last_event.time,
            last_domain=None if self._last_event is None else self._last_event.domain,
            last_kind=None if self._last_event is None else self._last_event.kind,
            status=self.status,
        )


class _SystemEvents:
    key = MonitorViewKeys.events
    schema = SYSTEM_EVENTS_SCHEMA

    def __init__(self) -> None:
        self._event_count = 0
        self._last_event: Message | None = None

    def on_event(self, event: Message) -> None:
        if event.domain == "system":
            self._event_count += 1
            self._last_event = event

    def view(self) -> SystemEventView:
        if self._last_event is None:
            return SystemEventView(event_count=self._event_count)
        payload = self._last_event.payload
        return SystemEventView(
            event_count=self._event_count,
            last_name=self._last_event.kind,
            last_event_time=self._last_event.time,
            last_payload=dict(payload) if isinstance(payload, Mapping) else {"type": type(payload).__name__},
        )


class _HealthState:
    key = MonitorViewKeys.health
    schema = HEALTH_SCHEMA

    def __init__(self, actor_states: object, actor_sources: object, connections: object) -> None:
        self._actor_states = actor_states
        self._actor_sources = actor_sources
        self._connections = connections
        self._event_count = 0
        self._last_event_time: datetime | None = None
        self._last_processed_at: datetime | None = None

    def on_event(self, event: Message) -> None:
        self._event_count += 1
        self._last_event_time = event.time

    def view(self) -> SystemHealthView:
        lifecycle = {item.actor: item for item in tuple(self._actor_states())}
        source_items = tuple(self._actor_sources())
        actors: list[ActorHealthView] = []
        for source in source_items:
            metrics = getattr(source, "runtime_metrics", lambda: {})()
            name = str(metrics.get("actor") or getattr(source, "name", "unknown"))
            state = lifecycle.get(name)
            processed_at = metrics.get("last_processed_at")
            if isinstance(processed_at, datetime) and (self._last_processed_at is None or processed_at > self._last_processed_at):
                self._last_processed_at = processed_at
            actors.append(ActorHealthView(
                actor=name,
                state="initialized" if state is None else state.state,
                started=bool(metrics.get("started", False)),
                stopping=bool(metrics.get("stopping", False)),
                processed_count=int(metrics.get("processed_count", 0) or 0),
                error_count=int(metrics.get("error_count", 0) or 0),
                mailbox_depth=int(metrics.get("mailbox_depth", 0) or 0),
                event_loops=int(metrics.get("event_loops", 0) or 0),
                last_processed_at=processed_at if isinstance(processed_at, datetime) else None,
                last_error=metrics.get("last_error") if isinstance(metrics.get("last_error"), str) else (None if state is None else state.error),
                last_event_time=None if state is None else state.at,
            ))
        for name, state in lifecycle.items():
            if not any(item.actor == name for item in actors):
                actors.append(ActorHealthView(name, state=state.state, last_error=state.error, last_event_time=state.at))
        connections = tuple(_connection_view(item) for item in tuple(self._connections()))
        error_count = sum(item.error_count for item in actors)
        failed = any(item.state == "failed" for item in actors)
        degraded = any(item.healthy is False or item.status in {"error", "degraded"} for item in connections)
        return SystemHealthView(
            status="failed" if failed else ("degraded" if degraded else "running"),
            event_count=self._event_count,
            error_count=error_count,
            last_event_time=self._last_event_time,
            last_processed_at=self._last_processed_at,
            actors=tuple(sorted(actors, key=lambda item: item.actor)),
            connections=connections,
            stale=False,
        )


class _FreshnessState:
    key = MonitorViewKeys.freshness
    schema = FRESHNESS_SCHEMA

    def __init__(self, stale_after: timedelta | None) -> None:
        self._times: dict[str, datetime] = {}
        self._stale_after = stale_after

    def on_event(self, event: Message) -> None:
        self._times[event.domain] = event.time

    def view(self, *, as_of: datetime | None = None) -> FreshnessView:
        stale_domains = ()
        if as_of is not None and self._stale_after is not None:
            stale_domains = tuple(sorted(domain for domain, at in self._times.items() if as_of - at > self._stale_after))
        return FreshnessView(
            last_event_time=max(self._times.values(), default=None),
            last_market_event_time=self._times.get("market"),
            last_account_event_time=self._times.get("account"),
            last_execution_event_time=self._times.get("execution"),
            last_risk_event_time=self._times.get("risk"),
            last_system_event_time=self._times.get("system"),
            stale_domains=stale_domains,
        )


class _OperationsState:
    key = MonitorViewKeys.operations
    schema = OPERATIONS_SCHEMA

    def __init__(self) -> None:
        self._items: dict[str, OperationMonitorView] = {}

    def on_event(self, event: Message) -> None:
        payload = event.payload
        value = getattr(payload, "update", payload) if isinstance(payload, Mapping) else payload
        identifiers = _identifiers(value)
        operation_id = identifiers.get("order_id") or identifiers.get("reservation_id") or identifiers.get("intent_id")
        if not operation_id and event.domain not in {"account", "execution", "risk"}:
            return
        operation_id = operation_id or f"{event.domain}:{event.kind}"
        old = self._items.get(operation_id)
        status = _status(value)
        stage = _stage(event, status)
        terminal = status in {"filled", "canceled", "rejected", "expired", "completed"}
        self._items[operation_id] = OperationMonitorView(
            operation_id=operation_id,
            stage=stage,
            status="completed" if terminal else ("rejected" if status == "rejected" else "active"),
            intent_id=identifiers.get("intent_id") or (None if old is None else old.intent_id),
            reservation_id=identifiers.get("reservation_id") or (None if old is None else old.reservation_id),
            order_id=identifiers.get("order_id") or (None if old is None else old.order_id),
            account_id=identifiers.get("account_id") or (None if old is None else old.account_id),
            first_event_time=event.time if old is None else old.first_event_time,
            last_event_time=event.time,
            last_topic=event.topic,
            error=_error(value) or (None if old is None else old.error),
            stale=False,
            correlation_id=event.correlation_id or (None if old is None else old.correlation_id),
            causation_id=event.causation_id or (None if old is None else old.causation_id),
        )

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        at = getattr(context, "now", None)
        for intent in intents:
            intent_id = str(getattr(intent, "intent_id", "") or "")
            if not intent_id:
                continue
            old = self._items.get(intent_id)
            self._items[intent_id] = OperationMonitorView(
                operation_id=intent_id,
                stage="intent_created",
                status="active",
                intent_id=intent_id,
                account_id=_text(getattr(intent, "account_id", None)),
                first_event_time=at if old is None else old.first_event_time,
                last_event_time=at if isinstance(at, datetime) else (None if old is None else old.last_event_time),
                last_topic=f"strategy.{hook or 'intent'}",
                correlation_id=intent_id,
            )

    def view(self) -> object:
        return OperationCollectionView(tuple(sorted(self._items.values(), key=lambda item: (item.last_event_time or datetime.min, item.operation_id))))


class _AlertsState:
    key = MonitorViewKeys.alerts
    schema = ALERTS_SCHEMA

    def __init__(self, freshness: _FreshnessState, operations: _OperationsState, stale_after: timedelta | None) -> None:
        self._freshness = freshness
        self._operations = operations
        self._stale_after = stale_after
        self._items: dict[str, AlertView] = {}
        self._facts: list[tuple[str, AlertView]] = []

    def on_event(self, event: Message) -> None:
        payload = event.payload
        actor = getattr(payload, "actor", None)
        state = str(getattr(payload, "state", "") or "")
        if actor and state:
            alert_id = f"actor_failed:{actor}"
            if state == "failed":
                self._raise(alert_id, "actor_failed", "critical", event.time, event.correlation_id, {"actor": str(actor), "error": getattr(payload, "error", None)})
            elif state in {"started", "stopped"}:
                self._resolve(alert_id, event.time)

    def view(self) -> AlertCollectionView:
        return AlertCollectionView(tuple(sorted(self._items.values(), key=lambda item: (item.status != "active", item.severity, item.alert_id))))

    def reconcile(self, as_of: datetime) -> tuple[AlertView, ...]:
        stale_domains = set(self._freshness.view(as_of=as_of).stale_domains)
        for domain in stale_domains:
            self._raise(f"freshness_stale:{domain}", "freshness_stale", "error", as_of, None, {"domain": domain})
        for domain in {"market", "account", "execution", "risk"} - stale_domains:
            self._resolve(f"freshness_stale:{domain}", as_of)
        operations = self._operations.view().operations
        stale_operations = set()
        if self._stale_after is not None:
            for operation in operations:
                if operation.status == "active" and operation.last_event_time is not None and as_of - operation.last_event_time > self._stale_after:
                    stale_operations.add(operation.operation_id)
                    self._raise(
                        f"operation_stale:{operation.operation_id}",
                        "operation_stale",
                        "warning",
                        as_of,
                        operation.correlation_id,
                        {"operation_id": operation.operation_id, "stage": operation.stage},
                    )
        for alert_id, alert in tuple(self._items.items()):
            if alert.rule == "operation_stale" and alert.alert_id.split(":", 1)[1] not in stale_operations:
                self._resolve(alert_id, as_of)
        return self.view().alerts

    def drain_facts(self) -> tuple[tuple[str, AlertView], ...]:
        facts = tuple(self._facts)
        self._facts.clear()
        return facts

    def _raise(self, alert_id: str, rule: str, severity: str, at: datetime, correlation_id: str | None, details: dict[str, object]) -> None:
        old = self._items.get(alert_id)
        if old is not None and old.status == "active":
            self._items[alert_id] = AlertView(alert_id, rule, severity, "active", old.first_seen, at, correlation_id=correlation_id or old.correlation_id, details=details)
            return
        alert = AlertView(alert_id, rule, severity, "active", at if old is None else old.first_seen, at, correlation_id=correlation_id, details=details)
        self._items[alert_id] = alert
        self._facts.append(("monitor.alert.raised", alert))

    def _resolve(self, alert_id: str, at: datetime) -> None:
        old = self._items.get(alert_id)
        if old is None or old.status == "resolved":
            return
        resolved = AlertView(old.alert_id, old.rule, old.severity, "resolved", old.first_seen, at, at, old.correlation_id, old.details)
        self._items[alert_id] = resolved
        self._facts.append(("monitor.alert.resolved", resolved))


def _connection_view(value: object) -> ConnectionHealthView:
    if isinstance(value, Mapping):
        resource = value.get("resource")
        nested = resource if isinstance(resource, Mapping) else {}
        name = str(value.get("connection") or value.get("key") or value.get("name") or value.get("id") or "connection")
        status = str(value.get("status") or nested.get("status") or "unknown")
        healthy = _bool(value.get("healthy"))
        if healthy is None: healthy = _bool(nested.get("healthy"))
        authenticated = _bool(value.get("authenticated"))
        if authenticated is None: authenticated = _bool(nested.get("authenticated"))
        reconnects = value.get("reconnect_count", value.get("reconnects", 0))
        return ConnectionHealthView(name, status, healthy, authenticated, int(reconnects or 0), _datetime(value.get("last_event_time")), _text(value.get("last_error") or nested.get("last_error")))
    return ConnectionHealthView(type(value).__name__, "unknown", None, None, 0, None, str(value))


def _identifiers(value: object) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in ("intent_id", "reservation_id", "order_id", "account_id"):
        candidate = getattr(value, name, None)
        if candidate is None and isinstance(value, Mapping):
            candidate = value.get(name)
        if candidate is not None and str(candidate):
            result[name] = str(candidate)
    for nested_name in ("request", "reservation", "assessment", "context"):
        nested = getattr(value, nested_name, None)
        if nested is None and isinstance(value, Mapping):
            nested = value.get(nested_name)
        if nested is not None:
            for name, candidate in _identifiers(nested).items():
                result.setdefault(name, candidate)
    return result


def _status(value: object) -> str:
    candidate = getattr(value, "status", None)
    if candidate is None:
        candidate = getattr(value, "kind", None)
    if candidate is None and isinstance(value, Mapping):
        candidate = value.get("status")
    return str(getattr(candidate, "value", candidate) or "").lower()


def _stage(event: Message, status: str) -> str:
    if event.domain == "risk": return "risk_" + ("completed" if status in {"released", "consumed"} else "reserved")
    if event.domain == "execution": return "execution_" + (status or event.kind)
    if event.domain == "account": return "account_" + event.kind
    return event.topic


def _error(value: object) -> str | None:
    candidate = getattr(value, "error", None)
    if candidate is None and isinstance(value, Mapping): candidate = value.get("error")
    return None if candidate is None else str(candidate)


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


__all__ = ["MonitorActorProjectors"]
