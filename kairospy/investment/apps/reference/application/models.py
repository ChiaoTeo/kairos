from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from kairospy.primitives.decimal import (
    Money,
    MoneyLike,
    Price,
    PriceLike,
    Quantity,
    QuantityLike,
    Rate,
    RateLike,
)
from kairospy.primitives.reference import (
    AssetId,
    ExchangeId,
    InstrumentId,
    ListingId,
    MarketId,
)


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
class Exchange:
    id: ExchangeId
    name: str
    status: ReferenceStatus = ReferenceStatus.UNKNOWN

    def __post_init__(self) -> None:
        if not str(self.id).strip() or not self.name.strip():
            raise ValueError("exchange id and name are required")


@dataclass(frozen=True, slots=True)
class Asset:
    id: AssetId
    code: str
    name: str | None
    asset_class: str
    status: ReferenceStatus = ReferenceStatus.UNKNOWN

    def __post_init__(self) -> None:
        if not str(self.id).strip() or not self.code.strip() or not self.asset_class.strip():
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
    product_family: str | None = None
    issuer_id: str | None = None
    share_class: str | None = None
    primary_currency_asset_id: AssetId | None = None
    underlying_instrument_id: InstrumentId | None = None
    expiry_unix_nanos: int | None = None
    strike: PriceLike | None = None
    option_right: str | None = None
    status: ReferenceStatus = ReferenceStatus.UNKNOWN

    def __post_init__(self) -> None:
        object.__setattr__(self, "strike", _price(self.strike))
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
    price_increment: PriceLike | None = None
    quantity_increment: QuantityLike | None = None
    minimum_quantity: QuantityLike | None = None
    minimum_notional: MoneyLike | None = None
    contract_multiplier: RateLike | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "price_increment", _price(self.price_increment))
        object.__setattr__(
            self, "quantity_increment", _quantity(self.quantity_increment)
        )
        object.__setattr__(
            self, "minimum_quantity", _quantity(self.minimum_quantity)
        )
        object.__setattr__(self, "minimum_notional", _money(self.minimum_notional))
        object.__setattr__(
            self, "contract_multiplier", _rate(self.contract_multiplier)
        )


@dataclass(frozen=True, slots=True)
class Market:
    id: MarketId
    instrument: InstrumentRef
    listing_id: ListingId | None
    exchange_id: ExchangeId
    instrument_kind: str
    venue_symbol: str | None = None
    base_asset: AssetId | None = None
    quote_asset: AssetId | None = None
    status: MarketStatus = MarketStatus.UNKNOWN
    trading_rules: TradingRules = field(default_factory=TradingRules)

    def __post_init__(self) -> None:
        if not self.instrument_kind.strip():
            raise ValueError("market instrument_kind is required")
        if self.venue_symbol is not None and not self.venue_symbol.strip():
            raise ValueError("market venue_symbol must be a non-empty string")


def _price(value: object | None) -> PriceLike | None:
    return _semantic(value, PriceLike, Price, "price")


def _quantity(value: object | None) -> QuantityLike | None:
    return _semantic(value, QuantityLike, Quantity, "quantity")


def _money(value: object | None) -> MoneyLike | None:
    return _semantic(value, MoneyLike, Money, "money")


def _rate(value: object | None) -> RateLike | None:
    return _semantic(value, RateLike, Rate, "rate")


def _semantic(value: object | None, protocol, concrete, name: str):
    if value is None or isinstance(value, protocol):
        return value
    if isinstance(value, (Decimal, str, int)) and not isinstance(value, bool):
        return concrete(value)
    raise TypeError(f"Reference {name} is invalid")
