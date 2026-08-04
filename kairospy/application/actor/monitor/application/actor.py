"""Runtime monitor Actor for Actor and Supervisor lifecycle state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.application.actor.support.base import BusinessActor
from kairospy.application.actor.support.lifecycle import ActorLifecycleEvent, SupervisorLifecycleEvent
from .projectors import MonitorActorProjectors
from kairospy.application.support.messaging import Message


@dataclass(frozen=True, slots=True)
class MonitorState:
    actor: str
    state: str
    at: datetime
    error: str | None = None


class MonitorActor(BusinessActor):
    """Observe runtime lifecycle without owning business decisions."""

    def __init__(self, *, strategy_id: str) -> None:
        super().__init__("monitor")
        self._actors: dict[str, MonitorState] = {}
        self._supervisor: SupervisorLifecycleEvent | None = None
        self.projectors = MonitorActorProjectors(
            strategy_id=strategy_id,
            actor_states=self.actors,
            supervisor_state=lambda: self._supervisor,
        )

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


__all__ = ["MonitorActor", "MonitorState"]
