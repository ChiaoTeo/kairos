from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ActorLifecycleEvent:
    actor: str
    state: str
    at: datetime
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SupervisorLifecycleEvent:
    state: str
    at: datetime
    actors: tuple[str, ...] = ()


__all__ = ["ActorLifecycleEvent", "SupervisorLifecycleEvent"]
