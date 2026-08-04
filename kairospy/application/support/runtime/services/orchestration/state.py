from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from kairospy.application.support.messaging import Message
from kairospy.application.support.runtime.application.views import ViewStore


@dataclass(frozen=True, slots=True)
class Callback:
    hook: str
    event_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    program_id: str
    event_count: int
    callbacks: tuple[Callback, ...]
    last_event: Message | None = None


@dataclass(frozen=True, slots=True)
class RuntimeCycle:
    """Observable result of processing one external runtime input."""

    as_of: datetime
    event: Message
    views: ViewStore
    dispatched: bool = False
    hook: str | None = None
    output: object | None = None

@dataclass(slots=True)
class RuntimeFrame:
    callbacks: list[Callback] = field(default_factory=list)
    event_count: int = 0
    last_event: Message | None = None
    finished: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeStores:
    program_state: Mapping[str, object] = field(default_factory=dict)
    views: ViewStore = field(default_factory=ViewStore)


__all__ = [
    "RuntimeFrame",
    "RuntimeResult",
    "RuntimeStores",
    "RuntimeCycle",
    "Callback",
]
