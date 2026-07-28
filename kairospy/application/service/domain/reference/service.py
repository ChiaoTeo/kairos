from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.core.reference import LifecycleEvent, MarketResolver, ReferenceCatalog

from .store import ReferenceStore


@dataclass(slots=True)
class ReferenceCatalogService:
    store: ReferenceStore
    default_venue: str | None = None
    default_market: str | None = None

    def catalog(self) -> ReferenceCatalog:
        return self.store.load_catalog()

    def resolver(self, *, as_of: datetime | None = None) -> MarketResolver:
        return MarketResolver(self.catalog(), default_venue=self.default_venue, default_market=self.default_market, as_of=as_of)

    def lifecycle_events(self) -> tuple[LifecycleEvent, ...]:
        return self.store.load_events()


__all__ = ["ReferenceCatalogService"]
