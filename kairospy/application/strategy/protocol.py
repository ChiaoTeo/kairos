from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol
from kairospy.strategy import StrategyProtocol
from .domain.messages import (
    RawEventEnvelope,
)


Strategy = StrategyProtocol


class EventStream(Protocol):
    stream_id: str

    def can_join(self, event_sequence: int) -> bool: ...
    def events(self, after_sequence: int = 0) -> AsyncIterator[RawEventEnvelope]: ...


class LifecycleJournal(Protocol):
    def append(self, record: object) -> None: ...
