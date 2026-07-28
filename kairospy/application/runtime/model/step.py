from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .data import RuntimeDataEnvelope


RuntimePhase = Literal["start", "market", "account", "execution", "clock", "system", "end"]


@dataclass(frozen=True, slots=True)
class StrategyCallbackInvocation:
    hook: str
    phase: RuntimePhase | str
    sequence: int | None
    envelope: RuntimeDataEnvelope | None = None


@dataclass(frozen=True, slots=True)
class RuntimeStep:
    envelope: RuntimeDataEnvelope
    hook: str
    phase: RuntimePhase | str
    callback_sequence: int | None
    is_market_event: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeStepResult:
    step: RuntimeStep | None
    intents: tuple[object, ...] = ()
    follow_up_events: tuple[RuntimeDataEnvelope, ...] = ()


__all__ = [
    "RuntimePhase",
    "RuntimeStep",
    "RuntimeStepResult",
    "StrategyCallbackInvocation",
]
