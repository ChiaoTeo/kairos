"""Read-only Strategy view of the owner-native Market contract.

These protocols intentionally add no Strategy DTOs and perform no runtime
conversion. Objects decoded by ``kairospy._native_market_contract`` satisfy
them structurally.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Protocol


class DecimalValue(Protocol):
    @property
    def mantissa(self) -> int: ...

    @property
    def scale(self) -> int: ...

    @property
    def value(self) -> Decimal: ...


class ObservationScope(Protocol):
    @property
    def kind(self) -> str: ...

    @property
    def market_id(self) -> str | None: ...

    @property
    def instrument_id(self) -> str | None: ...

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
    instrument_id: str
    provider: str
    bar_spec_id: str
    open: DecimalValue
    high: DecimalValue
    low: DecimalValue
    close: DecimalValue
    volume: DecimalValue | None
    source_observed_at_unix_nanos: int


class Quote(Protocol):
    scope: ObservationScope
    instrument_id: str
    provider: str
    bid_price: DecimalValue | None
    bid_quantity: DecimalValue | None
    ask_price: DecimalValue | None
    ask_quantity: DecimalValue | None
    source_observed_at_unix_nanos: int


class Trade(Protocol):
    scope: ObservationScope
    instrument_id: str
    provider: str
    price: DecimalValue
    quantity: DecimalValue
    source_observed_at_unix_nanos: int


class OptionGreeks(Protocol):
    scope: ObservationScope
    instrument_id: str
    provider: str
    expiry_unix_nanos: int | None
    strike: DecimalValue | None
    delta: DecimalValue | None
    gamma: DecimalValue | None
    vega: DecimalValue | None
    theta: DecimalValue | None
    implied_volatility: DecimalValue | None
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
    "MarketEvent",
    "MarketEventMetadata",
    "ObservationScope",
    "OptionGreeks",
    "Quote",
    "QuoteEvent",
    "Trade",
    "TradeEvent",
]
