from __future__ import annotations

from dataclasses import dataclass, field

from kairospy.application.runtime.protocol.events import RuntimeEnvelope


@dataclass(frozen=True, slots=True)
class Callback:
    hook: str
    event_sequence: int | None = None
    intent_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeRunResult:
    strategy_id: str
    event_count: int
    callbacks: tuple[Callback, ...]
    intent_count: int
    last_event: RuntimeEnvelope | None = None


@dataclass(slots=True)
class RuntimeFrame:
    callbacks: list[Callback] = field(default_factory=list)
    event_count: int = 0
    last_event: RuntimeEnvelope | None = None
    finished: bool = False


__all__ = ["RuntimeFrame", "RuntimeRunResult", "Callback"]
