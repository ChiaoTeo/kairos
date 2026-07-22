from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Generic, TypeAlias, TypeVar
from uuid import UUID, uuid4

from kairospy.identity import InstrumentId
from kairospy.market.types import (
    FundingRate,
    Greeks,
    IndexPrice,
    MarkPrice,
    OpenInterest,
    OptionChain,
    Quote,
    Trade,
    TradingStatus,
    VolatilitySurfacePoint,
)


@dataclass(frozen=True, slots=True)
class UnderlyingPriceUpdated:
    instrument_id: InstrumentId
    price: Decimal


@dataclass(frozen=True, slots=True)
class QuoteUpdated:
    quote: Quote


@dataclass(frozen=True, slots=True)
class TradeUpdated:
    trade: Trade


@dataclass(frozen=True, slots=True)
class GreeksUpdated:
    greeks: Greeks


@dataclass(frozen=True, slots=True)
class OptionChainDiscovered:
    chain: OptionChain


MarketPayload: TypeAlias = (
    UnderlyingPriceUpdated | QuoteUpdated | TradeUpdated | GreeksUpdated | OptionChainDiscovered
    | IndexPrice | MarkPrice | FundingRate | OpenInterest | TradingStatus | VolatilitySurfacePoint
)
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class EventEnvelope(Generic[T]):
    event_id: UUID
    event_time: datetime
    received_time: datetime
    payload: T
    source: str
    sequence: int | None = None
    correlation_id: UUID | None = None
    schema_version: int = 1
    raw_payload_reference: str | None = None

    def __post_init__(self) -> None:
        if self.event_time.tzinfo is None or self.received_time.tzinfo is None:
            raise ValueError("event timestamps must be timezone-aware")


MarketEvent: TypeAlias = EventEnvelope[MarketPayload]


def envelope(
    payload: T,
    *,
    source: str,
    event_time: datetime | None = None,
    received_time: datetime | None = None,
    correlation_id: UUID | None = None,
    sequence: int | None = None,
    raw_payload_reference: str | None = None,
) -> EventEnvelope[T]:
    now = datetime.now(timezone.utc)
    return EventEnvelope(
        uuid4(),
        event_time or now,
        received_time or now,
        payload,
        source,
        sequence,
        correlation_id,
        1,
        raw_payload_reference,
    )
