from __future__ import annotations

from datetime import datetime

from kairospy.application.usecases.reference.services.service import ReferenceService
from kairospy.domain.reference import LifecycleEvent, MarketResolver, ReferenceCatalog


class ReferenceApplication:
    """System-scoped public reference component backed by private services."""

    def __init__(self, store: object, *, default_venue: str | None = None, default_market: str | None = None) -> None:
        self._service = ReferenceService(store, default_venue=default_venue, default_market=default_market)

    def catalog(self, *, reload: bool = False) -> ReferenceCatalog:
        return self._service.catalog(reload=reload)

    def save_catalog(self, catalog: ReferenceCatalog) -> ReferenceCatalog:
        return self._service.save_catalog(catalog)

    def lifecycle_events(self, *, reload: bool = False) -> tuple[LifecycleEvent, ...]:
        return self._service.lifecycle_events(reload=reload)

    def resolver(self, *, as_of: datetime | None = None) -> MarketResolver:
        return self._service.resolver(as_of=as_of)


__all__ = ["ReferenceApplication"]
