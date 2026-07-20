"""Point-in-time reference data model for assets, products and tradable contracts."""

from .catalog import ReferenceCatalog
from .identity import (
    BenchmarkId, BrokerId, CalendarId, EntityId, InstitutionId, ListingId,
    LocationId, NetworkAssetId, NetworkId, ProductId, ProviderId, RailId,
    RouteId, SeriesId,
)
from .contracts import (
    AssetDefinition, AssetType, BenchmarkDefinition, BenchmarkType,
    ContractSeries, ContractSpec, EconomicProduct, EntityDefinition, EntityType,
    ExecutionRoute, InstrumentDefinition, InstrumentLifecycle,
    InstrumentReference, ListingDefinition, MappingTargetType,
    NetworkAssetDefinition, NetworkDefinition, NetworkType, ProviderSymbolMapping,
    RailType, ReferenceRole, ReferenceTarget, SettlementMethod, SettlementRail,
    SettlementTerms, SettlementTermsDefinition, TradingRules,
    VenueDefinition, VenueType,
)

__all__ = [
    "AssetDefinition", "AssetType", "BenchmarkDefinition", "BenchmarkId",
    "BenchmarkType", "BrokerId", "CalendarId", "ContractSeries", "ContractSpec",
    "EconomicProduct", "EntityDefinition", "EntityId", "EntityType",
    "ExecutionRoute", "InstitutionId", "InstrumentDefinition",
    "InstrumentLifecycle", "InstrumentReference", "ListingDefinition",
    "ListingId", "LocationId", "MappingTargetType", "NetworkAssetId",
    "NetworkAssetDefinition", "NetworkDefinition", "NetworkId", "NetworkType",
    "ProductId", "ProviderId", "ProviderSymbolMapping", "RailId", "RailType",
    "ReferenceCatalog", "ReferenceCatalogRepository",
    "ReferenceRole", "ReferenceTarget", "SettlementRail",
    "RouteId", "SeriesId", "SettlementMethod", "SettlementTerms", "SettlementTermsDefinition",
    "TradingRules", "VenueDefinition", "VenueType",
]


def __getattr__(name: str):
    if name == "ReferenceCatalogRepository":
        from .repository import ReferenceCatalogRepository
        return ReferenceCatalogRepository
    raise AttributeError(name)
