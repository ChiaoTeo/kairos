from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.context.control import ControlRequest
from kairospy.core.intent import IntentState

from .data import RuntimeDataEnvelope


@dataclass(frozen=True, slots=True)
class StrategyCallbackRecord:
    hook: str
    event_sequence: int | None
    intents: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class StrategyRunResult:
    strategy_id: str
    event_count: int
    intents: tuple[object, ...]
    callbacks: tuple[StrategyCallbackRecord, ...]
    last_event: RuntimeDataEnvelope | None
    runtime_event_count: int = 0
    last_runtime_event: RuntimeDataEnvelope | None = None
    intent_states: tuple[IntentState, ...] = ()
    control_requests: tuple[ControlRequest, ...] = ()


__all__ = ["StrategyCallbackRecord", "StrategyRunResult"]
