from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from .identity import InstrumentId, VenueId


@dataclass(frozen=True, slots=True)
class OptionChain:
    """A discovered option universe, not a tradable instrument definition."""

    underlying_id: InstrumentId
    venue_id: VenueId
    exchange: str
    trading_class: str
    multiplier: Decimal
    expirations: tuple[date, ...]
    strikes: tuple[Decimal, ...]


@dataclass(frozen=True, slots=True)
class Quote:
    instrument_id: InstrumentId
    bid: Decimal | None
    ask: Decimal | None
    bid_size: Decimal | None
    ask_size: Decimal | None
    event_time: datetime


@dataclass(frozen=True, slots=True)
class Trade:
    instrument_id: InstrumentId
    price: Decimal
    quantity: Decimal
    event_time: datetime


@dataclass(frozen=True, slots=True)
class Bar:
    instrument_id: InstrumentId
    start: datetime
    end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    instrument_id: InstrumentId
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    sequence: int
    event_time: datetime


@dataclass(frozen=True, slots=True)
class OrderBookDelta:
    instrument_id: InstrumentId
    bid_updates: tuple[OrderBookLevel, ...]
    ask_updates: tuple[OrderBookLevel, ...]
    first_sequence: int
    last_sequence: int
    event_time: datetime


@dataclass(frozen=True, slots=True)
class DerivativeMarketState:
    instrument_id: InstrumentId
    index_price: Decimal | None
    mark_price: Decimal | None
    funding_rate: Decimal | None
    next_funding_at: datetime | None
    open_interest: Decimal | None
    event_time: datetime


@dataclass(frozen=True, slots=True)
class IndexPrice:
    instrument_id: InstrumentId
    price: Decimal
    event_time: datetime


@dataclass(frozen=True, slots=True)
class MarkPrice:
    instrument_id: InstrumentId
    price: Decimal
    event_time: datetime


@dataclass(frozen=True, slots=True)
class FundingRate:
    instrument_id: InstrumentId
    rate: Decimal
    next_funding_at: datetime
    event_time: datetime


@dataclass(frozen=True, slots=True)
class OpenInterest:
    instrument_id: InstrumentId
    quantity: Decimal
    event_time: datetime


@dataclass(frozen=True, slots=True)
class Greeks:
    instrument_id: InstrumentId
    implied_volatility: Decimal | None
    delta: Decimal | None
    gamma: Decimal | None
    theta: Decimal | None
    vega: Decimal | None
    event_time: datetime


@dataclass(frozen=True, slots=True)
class VolatilitySurfacePoint:
    underlying_id: InstrumentId
    expiry: datetime
    strike: Decimal
    implied_volatility: Decimal
    delta: Decimal | None
    event_time: datetime
    source: str


class TradingState(StrEnum):
    OPEN = "open"
    HALTED = "halted"
    CLOSED = "closed"
    MAINTENANCE = "maintenance"
    DELISTED = "delisted"


@dataclass(frozen=True, slots=True)
class TradingStatus:
    instrument_id: InstrumentId
    state: TradingState
    reason: str | None
    event_time: datetime
