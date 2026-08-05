"""Runtime monitor Actor for Actor and Supervisor lifecycle state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from kairospy.application.actor.support.base import BusinessActor
from kairospy.application.actor.support.lifecycle import ActorLifecycleEvent, SupervisorLifecycleEvent
from .projectors import MonitorActorProjectors
from kairospy.application.support.messaging import Message, MessageBus


@dataclass(frozen=True, slots=True)
class MonitorState:
    actor: str
    state: str
    at: datetime
    error: str | None = None


class MonitorActor(BusinessActor):
    """Observe runtime lifecycle without owning business decisions."""

    def __init__(self, *, strategy_id: str, bus: MessageBus | None = None, freshness_stale_after: timedelta | None = timedelta(minutes=5)) -> None:
        super().__init__("monitor", bus=bus)
        self._actors: dict[str, MonitorState] = {}
        self._supervisor: SupervisorLifecycleEvent | None = None
        self._actor_sources: tuple[object, ...] = ()
        self._connection_health: tuple[object, ...] = ()
        self.projectors = MonitorActorProjectors(
            strategy_id=strategy_id,
            actor_states=self.actors,
            supervisor_state=lambda: self._supervisor,
            actor_sources=lambda: self._actor_sources,
            connections=lambda: self._connection_health,
            freshness_stale_after=freshness_stale_after,
        )

    def bind_actor_sources(self, actors: tuple[object, ...]) -> None:
        """Attach read-only runtime metric sources during composition."""
        self._actor_sources = tuple(actor for actor in actors if actor is not self)

    def record_connection_health(self, health: object) -> None:
        """Observe a connection-scope snapshot without owning the connection."""
        if isinstance(health, dict):
            items = health.get("items")
            self._connection_health = tuple(items) if isinstance(items, (tuple, list)) else (health,)
        elif isinstance(health, (tuple, list)):
            self._connection_health = tuple(health)
        else:
            self._connection_health = (health,)

    def actors(self) -> tuple[MonitorState, ...]:
        return tuple(self._actors.values())

    @property
    def supervisor(self) -> SupervisorLifecycleEvent | None:
        return self._supervisor

    async def process(self, message: Message) -> None:
        payload = message.payload
        if isinstance(payload, ActorLifecycleEvent):
            self._actors[payload.actor] = MonitorState(payload.actor, payload.state, payload.at, payload.error)
        elif isinstance(payload, SupervisorLifecycleEvent):
            self._supervisor = payload
        self.projectors.on_event(message)
        self.projectors.reconcile_alerts(message.time)
        if self.bus is not None:
            for topic, alert in self.projectors.drain_alert_facts():
                await self.bus.publish(
                    Message(
                        topic,
                        alert,
                        message.time,
                        "monitor.actor",
                        self._next_sequence(),
                        correlation_id=alert.correlation_id or message.correlation_id,
                        causation_id=message.message_id,
                    )
                )

    def record_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        self.projectors.on_intents(intents, context, hook)

    def _next_sequence(self) -> int:
        sequence = getattr(self, "_event_sequence", 0) + 1
        self._event_sequence = sequence
        return sequence


__all__ = ["MonitorActor", "MonitorState"]
