from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime

from kairospy.core.reference import LifecycleEvent, MarketResolver, ReferenceCatalog

from kairospy.application.usecases.reference.store import ReferenceStore
from kairospy.application.support.runtime.contracts import ReferenceRuntimeEnvelope


@dataclass(slots=True)
class ReferenceCatalogService:
    store: ReferenceStore
    default_venue: str | None = None
    default_market: str | None = None
    _catalog: ReferenceCatalog | None = field(default=None, init=False, repr=False)
    _lifecycle_events: tuple[LifecycleEvent, ...] | None = field(default=None, init=False, repr=False)

    async def events(self) -> AsyncIterator[ReferenceRuntimeEnvelope]:
        if False:
            yield

    def catalog(self) -> ReferenceCatalog:
        if self._catalog is None:
            self._catalog = self.store.load_catalog()
        return self._catalog

    def resolver(self, *, as_of: datetime | None = None) -> MarketResolver:
        return MarketResolver(self.catalog(), default_venue=self.default_venue, default_market=self.default_market, as_of=as_of)

    def lifecycle_events(self) -> tuple[LifecycleEvent, ...]:
        if self._lifecycle_events is None:
            self._lifecycle_events = self.store.load_events()
        return self._lifecycle_events


__all__ = ["ReferenceCatalogService"]
