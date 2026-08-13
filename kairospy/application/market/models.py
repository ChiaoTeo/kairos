from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from kairospy.application.reference.models import InstrumentRef
from kairospy.domain_types import MarketId


class AggressorSide(StrEnum):
    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Bar:
    market_id: MarketId
    instrument: InstrumentRef
    timeframe: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
    occurred_at: datetime
    occurred_at_unix_nanos: int
    source_id: str | None = None

    def __post_init__(self) -> None:
        if not self.timeframe.strip():
            raise ValueError("bar timeframe is required")
        if self.occurred_at.tzinfo is None:
            raise ValueError("bar occurred_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Quote:
    market_id: MarketId
    instrument: InstrumentRef
    bid_price: Decimal | None
    bid_quantity: Decimal | None
    ask_price: Decimal | None
    ask_quantity: Decimal | None
    occurred_at: datetime
    occurred_at_unix_nanos: int
    source_id: str | None = None


@dataclass(frozen=True, slots=True)
class Trade:
    market_id: MarketId
    instrument: InstrumentRef
    price: Decimal
    quantity: Decimal
    aggressor_side: AggressorSide | None
    occurred_at: datetime
    occurred_at_unix_nanos: int
    source_id: str | None = None


@dataclass(frozen=True, slots=True)
class OptionGreeks:
    market_id: MarketId
    instrument: InstrumentRef
    expiry_unix_nanos: int
    strike: Decimal | None
    delta: Decimal | None
    gamma: Decimal | None
    vega: Decimal | None
    theta: Decimal | None
    implied_volatility: Decimal | None
    occurred_at: datetime
    occurred_at_unix_nanos: int
    source_id: str | None = None
    derivation: str | None = None


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """One synchronous Market read model decoded from Market-owned storage."""

    view_key: str
    snapshot_id: str
    owner_actor_id: str
    generation: int
    quotes: tuple[Quote, ...] = ()
    trades: tuple[Trade, ...] = ()
    bars: tuple[Bar, ...] = ()
    greeks: tuple[OptionGreeks, ...] = ()

    def __post_init__(self) -> None:
        if not all(
            (
                self.view_key.strip(),
                self.snapshot_id.strip(),
                self.owner_actor_id.strip(),
            )
        ):
            raise ValueError("Market snapshot identity fields are required")
        if self.generation < 0:
            raise ValueError("Market snapshot generation cannot be negative")

    def latest_bar(self, market_id: MarketId, timeframe: str) -> Bar | None:
        return next(
            (
                value
                for value in self.bars
                if value.market_id == market_id and value.timeframe == timeframe
            ),
            None,
        )

    def latest_quote(self, market_id: MarketId) -> Quote | None:
        return next(
            (value for value in self.quotes if value.market_id == market_id), None
        )

    def latest_trade(self, market_id: MarketId) -> Trade | None:
        return next(
            (value for value in self.trades if value.market_id == market_id), None
        )

    def latest_greeks(self, market_id: MarketId) -> OptionGreeks | None:
        return next(
            (value for value in self.greeks if value.market_id == market_id), None
        )
