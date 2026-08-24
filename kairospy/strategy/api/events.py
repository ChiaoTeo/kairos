from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Mapping, TypeAlias

from kairospy.investment.application.eventing import DataEvent, EventMetadata

from .clock import TimerEvent


@dataclass(frozen=True, slots=True)
class TimerFiredEvent(DataEvent[TimerEvent]):
    kind: Literal["timer"] = field(init=False, default="timer")


@dataclass(frozen=True, slots=True)
class ClockAdvance:
    occurred_at: datetime
    source: str = "runtime"


@dataclass(frozen=True, slots=True)
class ClockAdvancedEvent(DataEvent[ClockAdvance]):
    kind: Literal["advance"] = field(init=False, default="advance")


ClockEvent: TypeAlias = TimerFiredEvent | ClockAdvancedEvent


@dataclass(frozen=True, slots=True)
class SystemNotice:
    code: str
    message: str
    details: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SystemEvent(DataEvent[SystemNotice]):
    kind: Literal["system"] = field(init=False, default="system")


__all__ = [
    "ClockAdvance",
    "ClockAdvancedEvent",
    "ClockEvent",
    "DataEvent",
    "EventMetadata",
    "SystemEvent",
    "SystemNotice",
    "TimerFiredEvent",
]
