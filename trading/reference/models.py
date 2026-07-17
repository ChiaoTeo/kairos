from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TypeAlias

from trading.domain.identity import AccountKey, AssetId, InstrumentId, VenueId
from trading.domain.product import (
    CryptoOptionSpec, CryptoSpotSpec, EquitySpec, FutureSpec, IndexSpec,
    ListedOptionSpec, PerpetualSpec, ProductType, SettlementSession,
    TokenizedEquitySpec,
)

from .identity import (
    BenchmarkId, BrokerId, CalendarId, EntityId, InstitutionId, ListingId,
    LocationId, NetworkAssetId, NetworkId, ProductId, ProviderId, RailId,
    RouteId, SeriesId,
)

ContractSpec: TypeAlias = (
    IndexSpec | EquitySpec | ListedOptionSpec | CryptoSpotSpec | FutureSpec
    | PerpetualSpec | CryptoOptionSpec | TokenizedEquitySpec
)

SPEC_TYPE_BY_PRODUCT_TYPE: dict[ProductType, type[ContractSpec]] = {
    ProductType.INDEX: IndexSpec,
    ProductType.EQUITY: EquitySpec,
    ProductType.ETF: EquitySpec,
    ProductType.LISTED_OPTION: ListedOptionSpec,
    ProductType.CRYPTO_SPOT: CryptoSpotSpec,
    ProductType.FUTURE: FutureSpec,
    ProductType.PERPETUAL: PerpetualSpec,
    ProductType.CRYPTO_OPTION: CryptoOptionSpec,
    ProductType.TOKENIZED_EQUITY: TokenizedEquitySpec,
}


def _valid_interval(start: datetime, end: datetime | None) -> None:
    if start.tzinfo is None or end is not None and end.tzinfo is None:
        raise ValueError("effective timestamps must be timezone-aware")
    if end is not None and end <= start:
        raise ValueError("effective_to must be after effective_from")


class AssetType(StrEnum):
    FIAT = "fiat"
    CRYPTO = "crypto"
    SECURITY = "security"
    FUND_SHARE = "fund_share"
    COMMODITY = "commodity"


class EntityType(StrEnum):
    ISSUER = "issuer"
    VENUE = "venue"
    BROKER = "broker"
    BANK = "bank"
    CUSTODIAN = "custodian"
    CLEARING_HOUSE = "clearing_house"
    BENCHMARK_ADMINISTRATOR = "benchmark_administrator"


class BenchmarkType(StrEnum):
    INDEX = "index"
    INTEREST_RATE = "interest_rate"
    FX_FIXING = "fx_fixing"
    MARK_PRICE = "mark_price"
    SETTLEMENT_VALUE = "settlement_value"


@dataclass(frozen=True, slots=True)
class AssetDefinition:
    asset_id: AssetId
    asset_type: AssetType
    name: str
    effective_from: datetime
    effective_to: datetime | None = None
    issuer_id: EntityId | None = None
    decimals: int | None = None

    def __post_init__(self) -> None:
        _valid_interval(self.effective_from, self.effective_to)
        if not self.name.strip():
            raise ValueError("asset name cannot be empty")
        if self.decimals is not None and self.decimals < 0:
            raise ValueError("asset decimals cannot be negative")

    def active_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


@dataclass(frozen=True, slots=True)
class EntityDefinition:
    entity_id: EntityId
    entity_type: EntityType
    legal_name: str
    effective_from: datetime
    effective_to: datetime | None = None
    country: str | None = None

    def __post_init__(self) -> None:
        _valid_interval(self.effective_from, self.effective_to)
        if not self.legal_name.strip():
            raise ValueError("entity legal name cannot be empty")

    def active_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


class VenueType(StrEnum):
    EXCHANGE = "exchange"
    ATS = "ats"
    CRYPTO_EXCHANGE = "crypto_exchange"
    OTC = "otc"


@dataclass(frozen=True, slots=True)
class VenueDefinition:
    venue_id: VenueId
    venue_type: VenueType
    name: str
    timezone: str
    effective_from: datetime
    effective_to: datetime | None = None
    mic: str | None = None
    calendar_id: CalendarId | None = None

    def __post_init__(self) -> None:
        _valid_interval(self.effective_from, self.effective_to)
        if not self.name.strip() or not self.timezone.strip():
            raise ValueError("venue name and timezone cannot be empty")

    def active_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


@dataclass(frozen=True, slots=True)
class BenchmarkDefinition:
    benchmark_id: BenchmarkId
    benchmark_type: BenchmarkType
    name: str
    currency: AssetId
    effective_from: datetime
    effective_to: datetime | None = None
    administrator_id: EntityId | None = None
    calendar_id: CalendarId | None = None

    def __post_init__(self) -> None:
        _valid_interval(self.effective_from, self.effective_to)
        if not self.name.strip():
            raise ValueError("benchmark name cannot be empty")

    def active_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


@dataclass(frozen=True, slots=True)
class EconomicProduct:
    product_id: ProductId
    product_type: ProductType
    name: str
    effective_from: datetime
    effective_to: datetime | None = None
    issuer_id: EntityId | None = None
    currency: AssetId | None = None

    def __post_init__(self) -> None:
        _valid_interval(self.effective_from, self.effective_to)
        if not self.name.strip():
            raise ValueError("product name cannot be empty")

    def active_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


@dataclass(frozen=True, slots=True)
class ContractSeries:
    series_id: SeriesId
    product_id: ProductId
    effective_from: datetime
    effective_to: datetime | None = None
    expiry: datetime | None = None
    trading_class: str | None = None

    def __post_init__(self) -> None:
        _valid_interval(self.effective_from, self.effective_to)
        if self.expiry is not None and self.expiry.tzinfo is None:
            raise ValueError("series expiry must be timezone-aware")

    def active_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


@dataclass(frozen=True, slots=True)
class InstrumentLifecycle:
    listed_at: datetime | None = None
    last_trade_at: datetime | None = None
    expires_at: datetime | None = None
    settles_at: datetime | None = None

    def __post_init__(self) -> None:
        values = tuple(value for value in (self.listed_at, self.last_trade_at, self.expires_at, self.settles_at) if value is not None)
        if any(value.tzinfo is None for value in values):
            raise ValueError("instrument lifecycle timestamps must be timezone-aware")
        if self.listed_at and self.last_trade_at and self.last_trade_at < self.listed_at:
            raise ValueError("last trade cannot precede listing")
        if self.last_trade_at and self.expires_at and self.expires_at < self.last_trade_at:
            raise ValueError("expiry cannot precede last trade")
        if self.expires_at and self.settles_at and self.settles_at < self.expires_at:
            raise ValueError("settlement cannot precede expiry")


@dataclass(frozen=True, slots=True)
class InstrumentDefinition:
    instrument_id: InstrumentId
    product_id: ProductId
    instrument_type: ProductType
    contract_spec: ContractSpec
    lifecycle: InstrumentLifecycle
    effective_from: datetime
    effective_to: datetime | None = None
    series_id: SeriesId | None = None
    display_name: str | None = None
    settlement_terms_id: str | None = None

    def __post_init__(self) -> None:
        _valid_interval(self.effective_from, self.effective_to)
        expected = SPEC_TYPE_BY_PRODUCT_TYPE[self.instrument_type]
        if not isinstance(self.contract_spec, expected):
            raise TypeError(
                f"{self.instrument_type.value} instrument requires {expected.__name__}, "
                f"got {type(self.contract_spec).__name__}"
            )
        if self.display_name is not None and not self.display_name.strip():
            raise ValueError("display name cannot be blank")

    def active_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


@dataclass(frozen=True, slots=True)
class TradingRules:
    price_increment: Decimal
    quantity_increment: Decimal
    minimum_quantity: Decimal
    maximum_quantity: Decimal | None = None
    minimum_notional: Decimal | None = None

    def __post_init__(self) -> None:
        if self.price_increment <= 0 or self.quantity_increment <= 0 or self.minimum_quantity <= 0:
            raise ValueError("price and quantity rules must be positive")
        if self.maximum_quantity is not None and self.maximum_quantity < self.minimum_quantity:
            raise ValueError("maximum quantity cannot be below minimum quantity")
        if self.minimum_notional is not None and self.minimum_notional <= 0:
            raise ValueError("minimum notional must be positive")


@dataclass(frozen=True, slots=True)
class ListingDefinition:
    listing_id: ListingId
    instrument_id: InstrumentId
    venue_id: VenueId
    trading_symbol: str
    trading_currency: AssetId
    trading_rules: TradingRules
    effective_from: datetime
    effective_to: datetime | None = None
    venue_instrument_id: str | None = None

    def __post_init__(self) -> None:
        _valid_interval(self.effective_from, self.effective_to)
        if not self.trading_symbol.strip():
            raise ValueError("listing trading symbol cannot be empty")

    def active_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


class MappingTargetType(StrEnum):
    PRODUCT = "product"
    INSTRUMENT = "instrument"
    LISTING = "listing"
    BENCHMARK = "benchmark"
    SERIES = "series"


@dataclass(frozen=True, slots=True)
class ProviderSymbolMapping:
    provider_id: ProviderId
    namespace: str
    external_id: str
    target_type: MappingTargetType
    target_id: str
    effective_from: datetime
    effective_to: datetime | None = None
    publisher_id: str | None = None

    def __post_init__(self) -> None:
        _valid_interval(self.effective_from, self.effective_to)
        if not self.namespace.strip() or not self.external_id.strip() or not self.target_id.strip():
            raise ValueError("provider mapping fields cannot be empty")

    def active_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


class ReferenceRole(StrEnum):
    ECONOMIC_UNDERLYING = "economic_underlying"
    PRICING_UNDERLYING = "pricing_underlying"
    SETTLEMENT_BENCHMARK = "settlement_benchmark"
    DELIVERABLE = "deliverable"
    REFERENCE_INSTRUMENT = "reference_instrument"
    HEDGE_PROXY = "hedge_proxy"


@dataclass(frozen=True, slots=True)
class ReferenceTarget:
    asset_id: AssetId | None = None
    instrument_id: InstrumentId | None = None
    benchmark_id: BenchmarkId | None = None
    product_id: ProductId | None = None

    def __post_init__(self) -> None:
        if sum(value is not None for value in (self.asset_id, self.instrument_id, self.benchmark_id, self.product_id)) != 1:
            raise ValueError("reference target must contain exactly one id")


@dataclass(frozen=True, slots=True)
class InstrumentReference:
    source_instrument_id: InstrumentId
    role: ReferenceRole
    target: ReferenceTarget
    effective_from: datetime
    effective_to: datetime | None = None
    weight: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        _valid_interval(self.effective_from, self.effective_to)
        if self.weight == 0:
            raise ValueError("reference weight cannot be zero")

    def active_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


class SettlementMethod(StrEnum):
    CASH = "cash"
    PHYSICAL = "physical"


@dataclass(frozen=True, slots=True)
class Deliverable:
    asset_id: AssetId
    quantity: Decimal

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("deliverable quantity must be positive")


@dataclass(frozen=True, slots=True)
class SettlementTerms:
    method: SettlementMethod
    session: SettlementSession
    settlement_asset: AssetId | None = None
    benchmark_id: BenchmarkId | None = None
    determination_at: datetime | None = None
    settlement_at: datetime | None = None
    deliverables: tuple[Deliverable, ...] = ()

    def __post_init__(self) -> None:
        if any(value is not None and value.tzinfo is None for value in (self.determination_at, self.settlement_at)):
            raise ValueError("settlement timestamps must be timezone-aware")
        if self.determination_at and self.settlement_at and self.settlement_at < self.determination_at:
            raise ValueError("settlement cannot precede determination")
        if self.method is SettlementMethod.CASH:
            if self.settlement_asset is None or self.benchmark_id is None or self.deliverables:
                raise ValueError("cash settlement requires asset and benchmark and no deliverables")
        elif not self.deliverables or self.benchmark_id is not None:
            raise ValueError("physical settlement requires deliverables and no benchmark")


@dataclass(frozen=True, slots=True)
class SettlementTermsDefinition:
    settlement_terms_id: str
    terms: SettlementTerms
    effective_from: datetime
    effective_to: datetime | None = None

    def __post_init__(self) -> None:
        _valid_interval(self.effective_from, self.effective_to)
        if not self.settlement_terms_id.strip():
            raise ValueError("settlement terms id cannot be empty")

    def active_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


@dataclass(frozen=True, slots=True)
class ExecutionRoute:
    route_id: RouteId
    broker_id: BrokerId
    account_key: AccountKey
    listing_id: ListingId
    effective_from: datetime
    effective_to: datetime | None = None
    broker_contract_id: str | None = None

    def __post_init__(self) -> None:
        _valid_interval(self.effective_from, self.effective_to)

    def active_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


class NetworkType(StrEnum):
    BLOCKCHAIN = "blockchain"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class NetworkDefinition:
    network_id: NetworkId
    network_type: NetworkType
    name: str
    effective_from: datetime
    effective_to: datetime | None = None
    native_asset: AssetId | None = None
    minimum_confirmations: int = 1

    def __post_init__(self) -> None:
        _valid_interval(self.effective_from, self.effective_to)
        if not self.name.strip() or self.minimum_confirmations < 0:
            raise ValueError("invalid network definition")

    def active_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


@dataclass(frozen=True, slots=True)
class NetworkAssetDefinition:
    network_asset_id: NetworkAssetId
    asset_id: AssetId
    network_id: NetworkId
    decimals: int
    effective_from: datetime
    effective_to: datetime | None = None
    contract_address: str | None = None
    deposit_enabled: bool = True
    withdrawal_enabled: bool = True
    minimum_withdrawal: Decimal | None = None
    withdrawal_fee: Decimal | None = None

    def __post_init__(self) -> None:
        _valid_interval(self.effective_from, self.effective_to)
        if self.decimals < 0:
            raise ValueError("network asset decimals cannot be negative")
        if self.minimum_withdrawal is not None and self.minimum_withdrawal <= 0:
            raise ValueError("minimum withdrawal must be positive")
        if self.withdrawal_fee is not None and self.withdrawal_fee < 0:
            raise ValueError("withdrawal fee cannot be negative")

    def active_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


class RailType(StrEnum):
    INTERNAL = "internal"
    BLOCKCHAIN = "blockchain"
    ACH = "ach"
    FEDWIRE = "fedwire"
    SWIFT = "swift"
    SEPA = "sepa"


@dataclass(frozen=True, slots=True)
class SettlementRail:
    rail_id: RailId
    rail_type: RailType
    supported_assets: tuple[AssetId, ...]
    effective_from: datetime
    effective_to: datetime | None = None
    calendar_id: CalendarId | None = None
    network_id: NetworkId | None = None
    minimum_amount: Decimal | None = None
    maximum_amount: Decimal | None = None

    def __post_init__(self) -> None:
        _valid_interval(self.effective_from, self.effective_to)
        if not self.supported_assets or len(set(self.supported_assets)) != len(self.supported_assets):
            raise ValueError("settlement rail requires unique supported assets")
        if self.rail_type is RailType.BLOCKCHAIN and self.network_id is None:
            raise ValueError("blockchain rail requires a network")
        if self.rail_type is not RailType.BLOCKCHAIN and self.network_id is not None:
            raise ValueError("only blockchain rail may reference a network")
        if self.minimum_amount is not None and self.minimum_amount <= 0:
            raise ValueError("rail minimum amount must be positive")
        if self.maximum_amount is not None and self.maximum_amount <= 0:
            raise ValueError("rail maximum amount must be positive")
        if self.minimum_amount and self.maximum_amount and self.maximum_amount < self.minimum_amount:
            raise ValueError("rail maximum cannot be below minimum")

    def active_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


@dataclass(frozen=True, slots=True)
class LocationDefinition:
    location_id: LocationId
    location_type: str
    effective_from: datetime
    effective_to: datetime | None = None
    institution_id: InstitutionId | None = None
    account_key: AccountKey | None = None
    network_id: NetworkId | None = None
    address: str | None = None

    def __post_init__(self) -> None:
        _valid_interval(self.effective_from, self.effective_to)
        if not self.location_type.strip():
            raise ValueError("location type cannot be empty")

    def active_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)
