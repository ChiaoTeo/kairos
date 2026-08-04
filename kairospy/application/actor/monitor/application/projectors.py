from __future__ import annotations

from datetime import datetime
from typing import Mapping

from kairospy.application.support.messaging import Message
from kairospy.application.support.runtime.application.views import ViewStore
from .views import (
    ACTOR_STATUS_SCHEMA,
    ActorStatusView,
    STRATEGY_LAUNCH_SCHEMA,
    SYSTEM_EVENTS_SCHEMA,
    SUPERVISOR_STATUS_SCHEMA,
    StrategyLaunchView,
    SupervisorStatusView,
    SystemEventView,
    MonitorViewKeys,
)


class MonitorActorProjectors:
    def __init__(self, *, strategy_id: str, actor_states: object, supervisor_state: object) -> None:
        self._actor_states = actor_states
        self._supervisor_state = supervisor_state
        self.runtime = _RuntimeState(strategy_id)
        self.events = _SystemEvents()
        self._states = (self.runtime, self.events)

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


__all__ = ["MonitorActorProjectors"]
