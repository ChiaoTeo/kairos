"""Point-in-time reference data model for assets, products and tradable contracts."""

from .catalog import ReferenceCatalog
from .identity import (
    BenchmarkId, BrokerId, CalendarId, EntityId, InstitutionId, ListingId,
    LocationId, NetworkAssetId, NetworkId, ProductId, ProviderId, RailId,
    RouteId, SeriesId,
)
from .contracts import (
    AssetDefinition, AssetType, BenchmarkDefinition, BenchmarkType,
    ContractSeries, ContractSpec, ContractType, CryptoOptionSpec, CryptoSpotSpec,
    EconomicProduct, EntityDefinition, EntityType, EquitySpec, ExerciseStyle,
    ExecutionRoute, FutureSpec, IndexSpec, InstrumentContractSpec,
    InstrumentDefinition, InstrumentLifecycle, InstrumentReference,
    ListedOptionSpec, ListingDefinition, MappingTargetType, NetworkAssetDefinition,
    NetworkDefinition, NetworkType, OptionRight, OptionSpec, PerpetualSpec,
    ProductType, ProviderSymbolMapping, RailType, ReferenceCapabilities,
    ReferenceRole, ReferenceTarget, SettlementMethod, SettlementRail, SettlementSession, SettlementTerms,
    SettlementTermsDefinition, SettlementType, TokenizedEquitySpec, TradingRules,
    VenueDefinition, VenueType,
    is_option_spec, option_multiplier,
)
from .ports import CorporateActionPort, ReferenceDataPort, ReferenceDataRequest

__all__ = [
    "AssetDefinition", "AssetType", "BenchmarkDefinition", "BenchmarkId",
    "BenchmarkType", "BrokerId", "CalendarId", "ContractSeries", "ContractSpec",
    "ContractType", "CryptoOptionSpec", "CryptoSpotSpec", "EconomicProduct",
    "EntityDefinition", "EntityId", "EntityType", "EquitySpec",
    "ExerciseStyle", "ExecutionRoute", "FutureSpec", "IndexSpec",
    "InstitutionId", "InstrumentContractSpec", "InstrumentDefinition",
    "InstrumentLifecycle", "InstrumentReference", "ListedOptionSpec",
    "ListingDefinition", "ListingId", "LocationId", "MappingTargetType",
    "NetworkAssetId", "NetworkAssetDefinition", "NetworkDefinition", "NetworkId",
    "NetworkType", "OptionRight", "OptionSpec", "PerpetualSpec", "ProductId",
    "ProductType", "ProviderId", "ProviderSymbolMapping", "RailId", "RailType",
    "CorporateActionPort", "ReferenceCapabilities", "ReferenceCatalog", "ReferenceCatalogRepository", "ReferenceDataPort",
    "ReferenceDataRequest", "ReferenceRole",
    "ReferenceTarget", "RouteId", "SeriesId", "SettlementMethod",
    "SettlementRail", "SettlementSession", "SettlementTerms",
    "SettlementTermsDefinition", "SettlementType", "TokenizedEquitySpec",
    "TradingRules", "VenueDefinition", "VenueType", "is_option_spec",
    "option_multiplier",
]


def __getattr__(name: str):
    if name == "ReferenceCatalogRepository":
        from .repository import ReferenceCatalogRepository
        return ReferenceCatalogRepository
    raise AttributeError(name)
