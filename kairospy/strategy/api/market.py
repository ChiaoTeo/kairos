"""Read-only Strategy view of the owner-native Market contract.

These protocols intentionally add no Strategy DTOs and perform no runtime
conversion. Objects decoded by ``kairospy._native_market_contract`` satisfy
them structurally.
"""

from __future__ import annotations

from typing import Literal, Protocol

from kairospy.primitives.decimal import (
    DecimalValue,
    PriceLike,
    QuantityLike,
    RateLike,
)
from kairospy.primitives.reference import InstrumentIdRead, MarketIdRead


class ObservationScope(Protocol):
    @property
    def kind(self) -> str: ...

    @property
    def market_id(self) -> MarketIdRead | None: ...

    @property
    def instrument_id(self) -> InstrumentIdRead | None: ...

    @property
    def network_id(self) -> str | None: ...


class MarketEventMetadata(Protocol):
    @property
    def stream_id(self) -> str: ...

    @property
    def sequence(self) -> int: ...

    @property
    def occurred_at_unix_nanos(self) -> int: ...


class Bar(Protocol):
    scope: ObservationScope
    instrument_id: InstrumentIdRead
    provider: str
    bar_spec_id: str
    open: PriceLike
    high: PriceLike
    low: PriceLike
    close: PriceLike
    volume: QuantityLike | None
    source_observed_at_unix_nanos: int


class Quote(Protocol):
    scope: ObservationScope
    instrument_id: InstrumentIdRead
    provider: str
    bid_price: PriceLike | None
    bid_quantity: QuantityLike | None
    ask_price: PriceLike | None
    ask_quantity: QuantityLike | None
    source_observed_at_unix_nanos: int


class Trade(Protocol):
    scope: ObservationScope
    instrument_id: InstrumentIdRead
    provider: str
    price: PriceLike
    quantity: QuantityLike
    source_observed_at_unix_nanos: int


class OptionGreeks(Protocol):
    scope: ObservationScope
    instrument_id: InstrumentIdRead
    provider: str
    expiry_unix_nanos: int | None
    strike: PriceLike | None
    delta: DecimalValue | None
    gamma: DecimalValue | None
    vega: DecimalValue | None
    theta: DecimalValue | None
    implied_volatility: RateLike | None
    source_observed_at_unix_nanos: int


class BarEvent(Protocol):
    kind: Literal["bar"]
    metadata: MarketEventMetadata
    data: Bar


class QuoteEvent(Protocol):
    kind: Literal["quote"]
    metadata: MarketEventMetadata
    data: Quote


class TradeEvent(Protocol):
    kind: Literal["trade"]
    metadata: MarketEventMetadata
    data: Trade


class GreeksEvent(Protocol):
    kind: Literal["greeks"]
    metadata: MarketEventMetadata
    data: OptionGreeks


class MarketEvent(Protocol):
    kind: str
    metadata: MarketEventMetadata
    data: object


__all__ = [
    "Bar",
    "BarEvent",
    "DecimalValue",
    "GreeksEvent",
    "InstrumentIdRead",
    "MarketEvent",
    "MarketEventMetadata",
    "MarketIdRead",
    "ObservationScope",
    "OptionGreeks",
    "PriceLike",
    "QuantityLike",
    "Quote",
    "QuoteEvent",
    "RateLike",
    "Trade",
    "TradeEvent",
]
