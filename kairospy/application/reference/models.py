from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from kairospy.domain_types import ExchangeId, InstrumentId, ListingId, MarketId


class MarketStatus(StrEnum):
    ACTIVE = "active"
    TRADING = "trading"
    INACTIVE = "inactive"
    HALTED = "halted"
    UNKNOWN = "unknown"


class ReferenceStatus(StrEnum):
    ACTIVE = "active"
    TRADING = "trading"
    DELISTED = "delisted"
    INACTIVE = "inactive"
    HALTED = "halted"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Entity:
    id: str
    entity_type: str
    name: str
    status: ReferenceStatus = ReferenceStatus.UNKNOWN

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.entity_type.strip() or not self.name.strip():
            raise ValueError("entity id, type, and name are required")


@dataclass(frozen=True, slots=True)
class Asset:
    id: str
    code: str
    name: str | None
    asset_class: str
    status: ReferenceStatus = ReferenceStatus.UNKNOWN

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.code.strip() or not self.asset_class.strip():
            raise ValueError("asset id, code, and class are required")


@dataclass(frozen=True, slots=True)
class InstrumentRef:
    id: InstrumentId
    display_symbol: str

    def __post_init__(self) -> None:
        if not self.display_symbol.strip():
            raise ValueError("instrument display_symbol is required")


@dataclass(frozen=True, slots=True)
class Instrument:
    id: InstrumentId
    symbol: str
    instrument_type: str
    name: str | None = None
    provider_product: str | None = None
    issuer_id: str | None = None
    share_class: str | None = None
    primary_currency_asset_id: str | None = None
    underlying_instrument_id: InstrumentId | None = None
    expiry_unix_nanos: int | None = None
    strike: Decimal | None = None
    option_right: str | None = None
    status: ReferenceStatus = ReferenceStatus.UNKNOWN

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.instrument_type.strip():
            raise ValueError("instrument symbol and type are required")

    @property
    def ref(self) -> InstrumentRef:
        return InstrumentRef(self.id, self.symbol)


@dataclass(frozen=True, slots=True)
class Listing:
    id: ListingId
    instrument_id: InstrumentId
    exchange_id: ExchangeId
    exchange_symbol: str
    status: ReferenceStatus = ReferenceStatus.UNKNOWN
    effective_from_unix_nanos: int = 0
    effective_to_unix_nanos: int | None = None

    def __post_init__(self) -> None:
        if not self.exchange_symbol.strip():
            raise ValueError("listing exchange_symbol is required")


@dataclass(frozen=True, slots=True)
class TradingRules:
    price_increment: Decimal | None = None
    quantity_increment: Decimal | None = None
    minimum_quantity: Decimal | None = None
    minimum_notional: Decimal | None = None
    contract_multiplier: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Market:
    id: MarketId
    instrument: InstrumentRef
    listing_id: ListingId | None
    exchange_id: ExchangeId
    instrument_kind: str
    venue_symbol: str | None = None
    base_asset: str | None = None
    quote_asset: str | None = None
    status: MarketStatus = MarketStatus.UNKNOWN
    trading_rules: TradingRules = TradingRules()

    def __post_init__(self) -> None:
        if not self.instrument_kind.strip():
            raise ValueError("market instrument_kind is required")
        if self.venue_symbol is not None and not self.venue_symbol.strip():
            raise ValueError("market venue_symbol must be a non-empty string")
