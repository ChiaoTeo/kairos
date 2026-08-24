from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from kairospy.investment.application.eventing import DataEvent
from kairospy.infrastructure.contracts.market.records import MarketEventRecord

from .models import Bar, OptionGreeks, Quote, Trade


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
