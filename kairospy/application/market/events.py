from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, TypeAlias

from kairospy.application.events import DataEvent

from .models import Bar, OptionGreeks, Quote, Trade


@dataclass(frozen=True, slots=True)
class MarketEventRecord:
    """Market-owned decoded contract record, private to the application slice."""

    stream_id: str
    sequence: int
    kind: str
    payload: object
    occurred_at: datetime | None = None
    schema_version: int = 1
    producer: str = "market"
    causation_id: str | None = None
    launch_id: str | None = None
    instance_id: str | None = None

    def __post_init__(self) -> None:
        if not self.stream_id.strip() or self.sequence <= 0:
            raise ValueError("Market event stream and positive sequence are required")
        if not self.kind.strip():
            raise ValueError("Market event kind is required")


class EventStreamGap(RuntimeError):
    """A Market stream skipped a sequence and requires event-native resync."""

    def __init__(self, stream_id: str, expected: int, actual: int) -> None:
        super().__init__(
            f"event stream {stream_id} gap: expected sequence {expected}, received {actual}"
        )
        self.stream_id = stream_id
        self.expected = expected
        self.actual = actual


@dataclass(frozen=True, slots=True)
class BarEvent(DataEvent[Bar]):
    kind: Literal["bar"] = field(init=False, default="bar")


@dataclass(frozen=True, slots=True)
class QuoteEvent(DataEvent[Quote]):
    kind: Literal["quote"] = field(init=False, default="quote")


@dataclass(frozen=True, slots=True)
class TradeEvent(DataEvent[Trade]):
    kind: Literal["trade"] = field(init=False, default="trade")


@dataclass(frozen=True, slots=True)
class GreeksEvent(DataEvent[OptionGreeks]):
    kind: Literal["greeks"] = field(init=False, default="greeks")


MarketEvent: TypeAlias = BarEvent | QuoteEvent | TradeEvent | GreeksEvent
