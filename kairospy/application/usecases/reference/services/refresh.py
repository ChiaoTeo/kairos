from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.domain.reference import ReferenceCatalog
from kairospy.application.usecases.reference.protocol import ReferenceStore
from kairospy.application.usecases.reference.application.results import ReferenceRefreshResult
from kairospy.application.usecases.reference.services.catalogs import ReferenceCatalogService

from ..domain.transition import apply_catalog_snapshot


@dataclass(slots=True)
class ReferenceRefreshService:
    store: ReferenceStore | ReferenceCatalogService

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
        underlying: str | None = None,
    ) -> ReferenceRefreshResult:
        """Apply a complete snapshot for the requested scope.

        Missing markets are treated as delisted only inside ``venue``,
        ``market`` and ``underlying``. A filtered provider response must never
        remove another underlying from the catalog.
        """
        catalogs = self.catalogs
        catalog = catalogs.catalog()
        transition = apply_catalog_snapshot(
            catalog,
            snapshot,
            as_of=as_of,
            venue=venue,
            market=market,
            underlying=underlying,
        )
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
