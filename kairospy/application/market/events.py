from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from kairospy.domain_types import DataEvent

from .models import Bar, Quote, Trade


@dataclass(frozen=True, slots=True)
class BarEvent(DataEvent[Bar]):
    kind: Literal["bar"] = field(init=False, default="bar")


@dataclass(frozen=True, slots=True)
class QuoteEvent(DataEvent[Quote]):
    kind: Literal["quote"] = field(init=False, default="quote")


@dataclass(frozen=True, slots=True)
class TradeEvent(DataEvent[Trade]):
    kind: Literal["trade"] = field(init=False, default="trade")


MarketEvent: TypeAlias = BarEvent | QuoteEvent | TradeEvent
