from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from kairospy.identity import AssetId, InstrumentId, VenueId


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


class DayCount(StrEnum):
    ACT_365 = "act_365"
    ACT_360 = "act_360"


class ForwardMethod(StrEnum):
    COST_OF_CARRY = "cost_of_carry"
    PUT_CALL_PARITY = "put_call_parity"
    VENDOR = "vendor"


@dataclass(frozen=True, slots=True)
class RateNode:
    maturity_years: Decimal
    zero_rate: Decimal

    def __post_init__(self) -> None:
        if self.maturity_years < 0:
            raise ValueError("rate maturity cannot be negative")


@dataclass(frozen=True, slots=True)
class RateCurve:
    as_of: datetime
    currency: AssetId
    nodes: tuple[RateNode, ...]
    day_count: DayCount
    source: str

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("rate curve as_of must be timezone-aware")
        if not self.nodes:
            raise ValueError("rate curve requires at least one node")
        if tuple(sorted(self.nodes, key=lambda item: item.maturity_years)) != self.nodes:
            raise ValueError("rate nodes must be ordered by maturity")


@dataclass(frozen=True, slots=True)
class DividendInput:
    as_of: datetime
    underlying_id: InstrumentId
    continuous_yield: Decimal
    source: str


@dataclass(frozen=True, slots=True)
class ForwardEstimate:
    as_of: datetime
    underlying_id: InstrumentId
    expiry: datetime
    value: Decimal
    method: ForwardMethod
    source: str

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None or self.expiry.tzinfo is None:
            raise ValueError("forward timestamps must be timezone-aware")
        if self.expiry <= self.as_of:
            raise ValueError("forward expiry must be after as_of")
        if self.value <= 0:
            raise ValueError("forward value must be positive")


@dataclass(frozen=True, slots=True)
class OptionMarketObservation:
    instrument_id: InstrumentId
    event_time: datetime
    bid: Decimal | None
    ask: Decimal | None
    bid_size: Decimal | None
    ask_size: Decimal | None
    source: str


@dataclass(frozen=True, slots=True)
class MarketQualityIssue:
    code: str
    severity: str
    message: str
    instrument_id: InstrumentId | None = None


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
