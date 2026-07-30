from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping, TypeAlias

from .model import Bar, MarketObservation, MarketSubject, OptionGreeks, Quote, RateObservation, TradePrint
from .orderbook import OrderBookDelta, OrderBookSnapshot


MarketEventValue: TypeAlias = Bar | Quote | OrderBookSnapshot | OrderBookDelta | TradePrint | RateObservation | OptionGreeks | MarketObservation


@dataclass(frozen=True, slots=True)
class MarketEvent:
    subject: MarketSubject
    observed_at: datetime
    value: MarketEventValue
    available_at: datetime | None = None
    source: str = ""
    sequence: int | None = None
    metadata: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("market event observed_at must be timezone-aware")
        if self.available_at is not None and self.available_at.tzinfo is None:
            raise ValueError("market event available_at must be timezone-aware")
        if self.sequence is not None and self.sequence < 1:
            raise ValueError("market event sequence must be positive")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def kind(self) -> str:
        value = self.value
        if isinstance(value, Quote):
            return "quote"
        if isinstance(value, OrderBookSnapshot):
            return "orderbook"
        if isinstance(value, OrderBookDelta):
            return "orderbook_delta"
        if isinstance(value, Bar):
            return "bar"
        if isinstance(value, TradePrint):
            return "trade"
        if isinstance(value, RateObservation):
            return "rate"
        if isinstance(value, OptionGreeks):
            return "option_greeks"
        return value.kind


__all__ = ["MarketEvent", "MarketEventValue"]
