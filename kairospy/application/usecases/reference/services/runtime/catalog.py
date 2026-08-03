from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime

from kairospy.domain.reference import LifecycleEvent, MarketResolver, ReferenceCatalog

from kairospy.application.usecases.reference.services.service import ReferenceService
from kairospy.application.support.runtime.domain.events import RuntimeEnvelope


@dataclass(slots=True)
class ReferenceCatalogService:
    store: object
    default_venue: str | None = None
    default_market: str | None = None
    reference: ReferenceService = field(init=False)

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        if False:
            yield

    def __post_init__(self) -> None:
        self.reference = ReferenceService(
            self.store,
            default_venue=self.default_venue,
            default_market=self.default_market,
        )

    def catalog(self) -> ReferenceCatalog:
        return self.reference.catalog()

    def resolver(self, *, as_of: datetime | None = None) -> MarketResolver:
        return self.reference.resolver(as_of=as_of)

    def lifecycle_events(self) -> tuple[LifecycleEvent, ...]:
        return self.reference.lifecycle_events()


__all__ = ["ReferenceCatalogService"]
