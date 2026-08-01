from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from kairospy.application.support.runtime.events import RuntimeEnvelope
from kairospy.core.intent import IntentJournal
from kairospy.core.views import ViewStore


@dataclass(frozen=True, slots=True)
class Callback:
    hook: str
    event_sequence: int | None = None
    intent_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeLaunchResult:
    strategy_id: str
    event_count: int
    callbacks: tuple[Callback, ...]
    intent_count: int
    last_event: RuntimeEnvelope | None = None


@dataclass(frozen=True, slots=True)
class RuntimeStep:
    kind: str
    as_of: datetime | None
    event: RuntimeEnvelope | None = None
    intents: tuple[object, ...] = ()
    traces: tuple[object, ...] = ()
    context: object | None = None
    hook: str = ""
    views: ViewStore | None = None


@dataclass(slots=True)
class RuntimeFrame:
    callbacks: list[Callback] = field(default_factory=list)
    event_count: int = 0
    last_event: RuntimeEnvelope | None = None
    finished: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeStores:
    strategy_state: Mapping[str, object] = field(default_factory=dict)
    intents: IntentJournal = field(default_factory=IntentJournal)
    views: ViewStore = field(default_factory=ViewStore)


__all__ = [
    "RuntimeFrame",
    "RuntimeLaunchResult",
    "RuntimeStores",
    "RuntimeStep",
    "Callback",
]
