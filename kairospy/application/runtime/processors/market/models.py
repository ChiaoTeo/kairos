from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class MarketQuoteSummary:
    market_id: str | None
    instrument_id: str
    market_key: str | None
    time: datetime
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    source: str = ""


@dataclass(frozen=True, slots=True)
class MarketQuotesView:
    event_count: int = 0
    quotes: tuple[MarketQuoteSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketRateSummary:
    rate_id: str
    time: datetime
    rate: Decimal
    source: str = ""
    tenor: str | None = None
    basis: str = ""
    market_id: str | None = None


@dataclass(frozen=True, slots=True)
class MarketRatesView:
    event_count: int = 0
    rates: tuple[MarketRateSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketBookSummary:
    market_id: str | None
    instrument_id: str
    market_key: str | None
    time: datetime
    bid1: Decimal | None = None
    ask1: Decimal | None = None
    bid_depth: int = 0
    ask_depth: int = 0
    bids: tuple[tuple[Decimal, Decimal], ...] = ()
    asks: tuple[tuple[Decimal, Decimal], ...] = ()
    nonce: object | None = None
    source: str = ""


@dataclass(frozen=True, slots=True)
class MarketBooksView:
    event_count: int = 0
    books: tuple[MarketBookSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketBarSummary:
    market_id: str | None
    instrument_id: str
    market_key: str | None
    time: datetime
    timeframe: str | None = None
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    volume: Decimal | None = None
    source: str = ""


@dataclass(frozen=True, slots=True)
class MarketBarsView:
    event_count: int = 0
    bars: tuple[MarketBarSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketTradeSummary:
    market_id: str | None
    instrument_id: str
    market_key: str | None
    time: datetime
    trade_id: str | None = None
    side: str | None = None
    price: Decimal | None = None
    size: Decimal | None = None
    cost: Decimal | None = None
    source: str = ""


@dataclass(frozen=True, slots=True)
class MarketTradesView:
    event_count: int = 0
    trades: tuple[MarketTradeSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketSubscriptionSummary:
    key: str
    subject_type: str
    subject_id: str
    kind: str
    status: str
    fields: tuple[str, ...] = ()
    requested_by: str = "strategy"
    provider: str = ""
    stream: str = ""
    last_event_time: datetime | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class MarketSubscriptionsView:
    total_count: int = 0
    active_count: int = 0
    subscriptions: tuple[MarketSubscriptionSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketFieldSummary:
    subject_type: str
    subject_id: str
    field: str
    observed_at: datetime
    value: object
    interval: str | None = None
    source: str = ""
    market_id: str | None = None
    market_key: str | None = None


@dataclass(frozen=True, slots=True)
class MarketFieldsView:
    event_count: int = 0
    fields: tuple[MarketFieldSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketObservationSummary:
    subject_type: str
    subject_id: str
    kind: str
    observed_at: datetime
    available_at: datetime | None = None
    source: str = ""
    sequence: int | None = None
    payload: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class MarketObservationsView:
    event_count: int = 0
    observations: tuple[MarketObservationSummary, ...] = ()


__all__ = [
    "MarketBarSummary",
    "MarketBarsView",
    "MarketBookSummary",
    "MarketBooksView",
    "MarketFieldSummary",
    "MarketFieldsView",
    "MarketObservationSummary",
    "MarketObservationsView",
    "MarketQuoteSummary",
    "MarketQuotesView",
    "MarketRateSummary",
    "MarketRatesView",
    "MarketSubscriptionSummary",
    "MarketSubscriptionsView",
    "MarketTradeSummary",
    "MarketTradesView",
]
