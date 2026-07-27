from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from kairospy.accounts import AccountContext, AccountProjection, AccountSnapshot


@dataclass(frozen=True, slots=True)
class MarketEvent:
    stream: str
    sequence: int
    time: datetime
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.stream.strip():
            raise ValueError("market event stream is required")
        if self.sequence < 1:
            raise ValueError("market event sequence must be positive")
        if self.time.tzinfo is None:
            raise ValueError("market event time must be timezone-aware")
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True, slots=True)
class ClockEvent:
    time: datetime
    name: str = "clock"
    payload: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.time.tzinfo is None:
            raise ValueError("clock event time must be timezone-aware")
        if not self.name.strip():
            raise ValueError("clock event name is required")
        object.__setattr__(self, "payload", dict(self.payload or {}))


@dataclass(frozen=True, slots=True)
class AccountRuntimeEvent:
    context: AccountContext
    sequence: int
    time: datetime
    payload: Mapping[str, object] | None = None
    snapshot: AccountSnapshot | None = None
    projection: AccountProjection | None = None
    stream: str = "account"

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("account runtime event sequence must be positive")
        if self.time.tzinfo is None:
            raise ValueError("account runtime event time must be timezone-aware")
        if self.snapshot is not None and self.snapshot.context != self.context:
            raise ValueError("account runtime event snapshot context does not match")
        if self.projection is not None and self.projection.context != self.context:
            raise ValueError("account runtime event projection context does not match")
        if not self.stream.strip():
            raise ValueError("account runtime event stream is required")
        object.__setattr__(self, "payload", dict(self.payload or {}))


@dataclass(frozen=True, slots=True)
class SystemRuntimeEvent:
    name: str
    sequence: int
    time: datetime
    payload: Mapping[str, object] | None = None
    stream: str = "system"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("system runtime event name is required")
        if self.sequence < 1:
            raise ValueError("system runtime event sequence must be positive")
        if self.time.tzinfo is None:
            raise ValueError("system runtime event time must be timezone-aware")
        if not self.stream.strip():
            raise ValueError("system runtime event stream is required")
        object.__setattr__(self, "payload", dict(self.payload or {}))


def parse_event_time(value: object) -> datetime:
    if isinstance(value, datetime):
        event_time = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        event_time = datetime.fromisoformat(text)
    if event_time.tzinfo is None:
        raise ValueError("event time must be timezone-aware")
    return event_time


RuntimeEvent = MarketEvent | ClockEvent | AccountRuntimeEvent | SystemRuntimeEvent


__all__ = [
    "AccountRuntimeEvent",
    "ClockEvent",
    "MarketEvent",
    "RuntimeEvent",
    "SystemRuntimeEvent",
    "parse_event_time",
]
