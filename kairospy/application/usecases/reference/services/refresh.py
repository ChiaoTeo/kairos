from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.domain.reference import LifecycleEvent, MarketDefinition, ReferenceCatalog
from kairospy.application.usecases.reference.services.catalogs import ReferenceCatalogService

from ..domain.transition import apply_catalog_snapshot


@dataclass(frozen=True, slots=True)
class ReferenceRefreshResult:
    catalog: ReferenceCatalog
    events: tuple[LifecycleEvent, ...]
    previous_markets: tuple[MarketDefinition, ...]
    current_markets: tuple[MarketDefinition, ...]


@dataclass(slots=True)
class ReferenceRefreshService:
    store: object | ReferenceCatalogService

    @property
    def catalogs(self) -> ReferenceCatalogService:
        if isinstance(self.store, ReferenceCatalogService):
            return self.store
        return ReferenceCatalogService(self.store)

    def refresh_snapshot(
        self,
        snapshot: ReferenceCatalog,
        *,
        as_of: datetime,
        venue: str | None = None,
        market: str | None = None,
    ) -> ReferenceRefreshResult:
        catalogs = self.catalogs
        catalog = catalogs.catalog()
        transition = apply_catalog_snapshot(catalog, snapshot, as_of=as_of, venue=venue, market=market)
        result = ReferenceRefreshResult(
            transition.catalog,
            transition.events,
            transition.previous_markets,
            transition.current_markets,
        )
        catalogs.save_catalog(result.catalog)
        catalogs.append_events(result.events)
        return result


__all__ = ["ReferenceRefreshResult", "ReferenceRefreshService"]
