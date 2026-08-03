from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from kairospy.application.usecases.reference.services.builders import (
    ReferenceSnapshot,
    ReferenceSnapshotBuilderService,
    catalog_from_equity_rows,
    catalog_from_market_rows,
    catalog_from_reference_rows,
    market_definitions_from_rows,
)
from kairospy.application.usecases.reference.services.catalogs import ReferenceCatalogService
from kairospy.application.usecases.reference.services.operations import (
    ReferenceProviderRefreshResult,
    ReferenceSourceRefreshResult,
    add_asset,
    refresh_equity_provider,
    refresh_exchange_reference,
    refresh_exchange_reference_with_delist_schedule,
    refresh_instrument_provider,
    refresh_instrument_provider_with_delist_schedule,
    refresh_provider_reference,
    sync_lifecycle_events,
)
from kairospy.application.usecases.reference.services.projections import ReferenceProjectionService
from kairospy.application.usecases.reference.services.resolution import ReferenceResolutionService
from kairospy.application.usecases.reference.domain.serde import (
    asset_from_primitive,
    asset_to_primitive,
    entity_from_primitive,
    entity_to_primitive,
    instrument_from_primitive,
    instrument_to_primitive,
    lifecycle_event_from_primitive,
    lifecycle_event_to_primitive,
    listing_from_primitive,
    listing_to_primitive,
    market_from_primitive,
    market_to_primitive,
)
from kairospy.domain.reference import LifecycleEvent, MarketResolver, ReferenceCatalog


@dataclass(slots=True)
class ReferenceService:
    """Application facade for reference data use cases."""

    store: object
    default_venue: str | None = None
    default_market: str | None = None
    catalogs: ReferenceCatalogService = field(init=False)

    def __post_init__(self) -> None:
        self.catalogs = ReferenceCatalogService(self.store)

    def catalog(self, *, reload: bool = False) -> ReferenceCatalog:
        return self.catalogs.catalog(reload=reload)

    def save_catalog(self, catalog: ReferenceCatalog) -> ReferenceCatalog:
        return self.catalogs.save_catalog(catalog)

    def lifecycle_events(self, *, reload: bool = False) -> tuple[LifecycleEvent, ...]:
        return self.catalogs.lifecycle_events(reload=reload)

    def resolver(self, *, as_of: datetime | None = None) -> MarketResolver:
        return ReferenceResolutionService(
            self.catalog(),
            default_venue=self.default_venue,
            default_market=self.default_market,
        ).resolver(as_of=as_of)


__all__ = [
    "ReferenceProjectionService",
    "ReferenceProviderRefreshResult",
    "ReferenceService",
    "ReferenceSnapshot",
    "ReferenceSnapshotBuilderService",
    "ReferenceSourceRefreshResult",
    "add_asset",
    "asset_from_primitive",
    "asset_to_primitive",
    "catalog_from_equity_rows",
    "catalog_from_market_rows",
    "catalog_from_reference_rows",
    "entity_from_primitive",
    "entity_to_primitive",
    "instrument_from_primitive",
    "instrument_to_primitive",
    "lifecycle_event_from_primitive",
    "lifecycle_event_to_primitive",
    "listing_from_primitive",
    "listing_to_primitive",
    "market_from_primitive",
    "market_definitions_from_rows",
    "market_to_primitive",
    "refresh_equity_provider",
    "refresh_exchange_reference",
    "refresh_exchange_reference_with_delist_schedule",
    "refresh_instrument_provider",
    "refresh_instrument_provider_with_delist_schedule",
    "refresh_provider_reference",
    "sync_lifecycle_events",
]
