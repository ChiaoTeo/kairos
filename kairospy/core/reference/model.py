from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Mapping

from .identity import AssetId, EntityId, InstrumentId, ListingId, MarketId


class EntityType(StrEnum):
    COMPANY = "company"
    NETWORK = "network"
    VENUE = "venue"
    ISSUER = "issuer"
    OTHER = "other"


class AssetType(StrEnum):
    EQUITY = "equity"
    CRYPTO = "crypto"
    FIAT = "fiat"
    FUND = "fund"
    INDEX = "index"
    OTHER = "other"


class InstrumentType(StrEnum):
    EQUITY = "equity"
    SPOT = "spot"
    PERPETUAL = "perpetual"
    FUTURE = "future"
    OPTION = "option"
    CASH = "cash"
    INDEX = "index"
    OTHER = "other"


class MarketStatus(StrEnum):
    PRE_LISTING = "pre_listing"
    ACTIVE = "active"
    HALTED = "halted"
    DELISTED = "delisted"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class LifecycleEventType(StrEnum):
    LISTED = "listed"
    DELISTED = "delisted"
    STATUS_CHANGED = "status_changed"
    SYMBOL_CHANGED = "symbol_changed"
    SPLIT = "split"
    DIVIDEND = "dividend"
    MERGER = "merger"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True, kw_only=True)
class EffectiveInterval:
    effective_from: datetime
    effective_to: datetime | None = None

    def __post_init__(self) -> None:
        start = _aware_utc(self.effective_from)
        end = None if self.effective_to is None else _aware_utc(self.effective_to)
        if end is not None and end <= start:
            raise ValueError("effective_to must be after effective_from")
        object.__setattr__(self, "effective_from", start)
        object.__setattr__(self, "effective_to", end)

    def active_at(self, at: datetime) -> bool:
        value = _aware_utc(at)
        return self.effective_from <= value and (self.effective_to is None or value < self.effective_to)


@dataclass(frozen=True, slots=True)
class Entity(EffectiveInterval):
    entity_id: EntityId
    entity_type: EntityType
    name: str
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Asset(EffectiveInterval):
    asset_id: AssetId
    asset_type: AssetType
    symbol: str
    name: str | None = None
    issuer_id: EntityId | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InstrumentDefinition(EffectiveInterval):
    instrument_id: InstrumentId
    instrument_type: InstrumentType
    base_asset_id: AssetId | None = None
    quote_asset_id: AssetId | None = None
    display_name: str | None = None
    underlying_instrument_id: InstrumentId | None = None
    expiry: datetime | None = None
    strike: Decimal | None = None
    option_right: str | None = None
    multiplier: Decimal | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ListingDefinition(EffectiveInterval):
    listing_id: ListingId
    instrument_id: InstrumentId
    venue: str
    trading_symbol: str
    venue_instrument_id: str | None = None
    currency_asset_id: AssetId | None = None
    status: MarketStatus = MarketStatus.ACTIVE
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MarketDefinition(EffectiveInterval):
    market_id: MarketId
    instrument_id: InstrumentId
    listing_id: ListingId
    venue: str
    market: str
    source_symbol: str
    status: MarketStatus = MarketStatus.ACTIVE
    price_tick: Decimal | None = None
    amount_tick: Decimal | None = None
    price_precision: int | None = None
    amount_precision: int | None = None
    min_amount: Decimal | None = None
    min_notional: Decimal | None = None
    contract_size: Decimal | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    event_type: LifecycleEventType
    event_time: datetime
    instrument_id: InstrumentId | None = None
    listing_id: ListingId | None = None
    market_id: MarketId | None = None
    venue: str | None = None
    source_symbol: str | None = None
    previous: Mapping[str, object] = field(default_factory=dict)
    current: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_time", _aware_utc(self.event_time))


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"time must be timezone-aware: {value!r}")
    return value.astimezone(timezone.utc)


__all__ = [
    "Asset",
    "AssetType",
    "EffectiveInterval",
    "Entity",
    "EntityType",
    "InstrumentDefinition",
    "InstrumentType",
    "LifecycleEvent",
    "LifecycleEventType",
    "ListingDefinition",
    "MarketDefinition",
    "MarketStatus",
]
