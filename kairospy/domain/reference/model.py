from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Mapping

from .identity import AssetId, EntityId, ExchangeId, FinancialProductId, InstrumentId, ListingId, MarketId, MarketTypeId, ProviderId, SourceSymbol


class EntityType(StrEnum):
    """Legal or operational participant class used by reference entities."""

    COMPANY = "company"
    NETWORK = "network"
    VENUE = "venue"
    ISSUER = "issuer"
    OTHER = "other"


class AssetType(StrEnum):
    """Asset family used for stable asset identity and instrument construction."""

    EQUITY = "equity"
    CRYPTO = "crypto"
    FIAT = "fiat"
    FUND = "fund"
    INDEX = "index"
    OTHER = "other"


class InstrumentType(StrEnum):
    """Tradable contract shape independent of venue listing details."""

    EQUITY = "equity"
    SPOT = "spot"
    PERPETUAL = "perpetual"
    FUTURE = "future"
    OPTION = "option"
    CASH = "cash"
    INDEX = "index"
    OTHER = "other"


class ProductFamily(StrEnum):
    """Trading and account product family, independent of asset type."""

    SPOT = "spot"
    USD_M_FUTURES = "usd_m_futures"
    COIN_M_FUTURES = "coin_m_futures"
    OPTIONS = "options"


class FinancialProductType(StrEnum):
    SIMPLE_EARN_FLEXIBLE = "simple_earn_flexible"
    SIMPLE_EARN_LOCKED = "simple_earn_locked"
    STAKING = "staking"
    DUAL_INVESTMENT = "dual_investment"
    DISCOUNT_BUY = "discount_buy"


class FinancialProductStatus(StrEnum):
    AVAILABLE = "available"
    SUBSCRIPTION_ONLY = "subscription_only"
    REDEMPTION_ONLY = "redemption_only"
    SUSPENDED = "suspended"
    MATURED = "matured"
    UNKNOWN = "unknown"


class MarketStatus(StrEnum):
    """Lifecycle/trading state for a listing or venue market at an as-of time."""

    PRE_LISTING = "pre_listing"
    ACTIVE = "active"
    HALTED = "halted"
    DELISTED = "delisted"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class LifecycleEventType(StrEnum):
    """Reference data change type recorded in lifecycle event history."""

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
    """Version interval for reference records.

    Reference records are immutable versions. A record is active when the
    requested as-of time falls inside this interval.
    """

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
    """Named real-world participant such as a venue operator, issuer, or company."""

    entity_id: EntityId
    entity_type: EntityType
    name: str
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Asset(EffectiveInterval):
    """Economic asset identity, independent of venue-specific tradable contracts."""

    asset_id: AssetId
    asset_type: AssetType
    symbol: str
    name: str | None = None
    issuer_id: EntityId | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InstrumentDefinition(EffectiveInterval):
    """Provider-neutral tradable instrument definition.

    An instrument describes what is traded, for example BTC/USDT spot or an
    equity share, before attaching it to a venue-specific listing.
    """

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
class FinancialProductDefinition(EffectiveInterval):
    """Provider-neutral identity and terms for a non-market investment product."""

    product_id: FinancialProductId
    product_type: FinancialProductType
    name: str
    asset_id: AssetId
    provider_product_id: str
    provider_id: ProviderId | str | None = None
    issuer_id: EntityId | None = None
    currency_asset_id: AssetId | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    apr: Decimal | None = None
    lock_period_days: int | None = None
    maturity_at: datetime | None = None
    status: FinancialProductStatus = FinancialProductStatus.UNKNOWN
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        EffectiveInterval.__post_init__(self)
        object.__setattr__(self, "product_id", _id(self.product_id, FinancialProductId, "product_id"))
        object.__setattr__(self, "product_type", FinancialProductType(str(self.product_type).lower()))
        object.__setattr__(self, "asset_id", _id(self.asset_id, AssetId, "asset_id"))
        object.__setattr__(self, "provider_product_id", _required_text(self.provider_product_id, "provider_product_id"))
        object.__setattr__(self, "provider_id", None if self.provider_id is None else _id(self.provider_id, ProviderId, "provider_id"))
        object.__setattr__(self, "issuer_id", None if self.issuer_id is None else _id(self.issuer_id, EntityId, "issuer_id"))
        object.__setattr__(self, "status", FinancialProductStatus(str(self.status).lower()))
        if self.min_amount is not None and self.min_amount < 0:
            raise ValueError("min_amount cannot be negative")
        if self.max_amount is not None and self.max_amount < 0:
            raise ValueError("max_amount cannot be negative")
        if self.min_amount is not None and self.max_amount is not None and self.max_amount < self.min_amount:
            raise ValueError("max_amount must be greater than or equal to min_amount")
        if self.lock_period_days is not None and self.lock_period_days < 0:
            raise ValueError("lock_period_days cannot be negative")


@dataclass(frozen=True, slots=True)
class ListingDefinition(EffectiveInterval):
    """Venue listing for an instrument.

    A listing binds a provider-neutral instrument to an exchange namespace and
    the symbol the venue uses for trading or lookup.
    """

    listing_id: ListingId
    instrument_id: InstrumentId
    venue: ExchangeId | str
    trading_symbol: SourceSymbol | str
    venue_instrument_id: SourceSymbol | str | None = None
    currency_asset_id: AssetId | None = None
    status: MarketStatus = MarketStatus.ACTIVE
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        EffectiveInterval.__post_init__(self)
        object.__setattr__(self, "venue", _id(self.venue, ExchangeId, "venue"))
        object.__setattr__(self, "trading_symbol", _id(self.trading_symbol, SourceSymbol, "trading_symbol"))
        object.__setattr__(
            self,
            "venue_instrument_id",
            None if self.venue_instrument_id is None else _id(self.venue_instrument_id, SourceSymbol, "venue_instrument_id"),
        )

    @property
    def exchange_id(self) -> ExchangeId:
        return self.venue

    @property
    def listing_symbol(self) -> SourceSymbol:
        return self.trading_symbol

    @property
    def exchange_instrument_id(self) -> SourceSymbol | None:
        return self.venue_instrument_id


@dataclass(frozen=True, slots=True)
class MarketDefinition(EffectiveInterval):
    """Tradable market endpoint on a venue.

    A market binds an instrument listing to a concrete venue/product namespace
    such as binance spot BTC/USDT. This is the identity strategies and runtime
    services resolve before subscribing, replaying, or trading.
    """

    market_id: MarketId
    instrument_id: InstrumentId
    listing_id: ListingId
    venue: ExchangeId | str
    market: MarketTypeId | str
    source_symbol: SourceSymbol | str
    status: MarketStatus = MarketStatus.ACTIVE
    price_tick: Decimal | None = None
    amount_tick: Decimal | None = None
    price_precision: int | None = None
    amount_precision: int | None = None
    min_amount: Decimal | None = None
    min_notional: Decimal | None = None
    contract_size: Decimal | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        EffectiveInterval.__post_init__(self)
        object.__setattr__(self, "venue", _id(self.venue, ExchangeId, "venue"))
        object.__setattr__(self, "market", _id(self.market, MarketTypeId, "market"))
        object.__setattr__(self, "source_symbol", _id(self.source_symbol, SourceSymbol, "source_symbol"))

    @property
    def exchange_id(self) -> ExchangeId:
        return self.venue

    @property
    def market_type(self) -> MarketTypeId:
        return self.market


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    """Reference history event for listings, markets, and instrument actions."""

    event_type: LifecycleEventType
    event_time: datetime
    instrument_id: InstrumentId | None = None
    listing_id: ListingId | None = None
    market_id: MarketId | None = None
    venue: ExchangeId | str | None = None
    source_symbol: SourceSymbol | str | None = None
    previous: Mapping[str, object] = field(default_factory=dict)
    current: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_time", _aware_utc(self.event_time))
        object.__setattr__(self, "venue", None if self.venue is None else _id(self.venue, ExchangeId, "venue"))
        object.__setattr__(self, "source_symbol", None if self.source_symbol is None else _id(self.source_symbol, SourceSymbol, "source_symbol"))

    @property
    def exchange_id(self) -> ExchangeId | None:
        return self.venue


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"time must be timezone-aware: {value!r}")
    return value.astimezone(timezone.utc)


def _id(value, id_type, label: str):
    if isinstance(value, id_type):
        return value
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} is required")
    return id_type(text)


def _required_text(value: object, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


__all__ = [
    "Asset",
    "AssetType",
    "EffectiveInterval",
    "Entity",
    "EntityType",
    "InstrumentDefinition",
    "InstrumentType",
    "ProductFamily",
    "FinancialProductDefinition",
    "FinancialProductStatus",
    "FinancialProductType",
    "LifecycleEvent",
    "LifecycleEventType",
    "ListingDefinition",
    "MarketDefinition",
    "MarketStatus",
]
