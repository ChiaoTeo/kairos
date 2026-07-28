from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.reference import LifecycleEvent, MarketResolver, ReferenceCatalog
from kairospy.core.views import ViewFieldSchema, ViewSchema

from .component import RuntimeViewPublisher


class ReferenceService(Protocol):
    def catalog(self) -> ReferenceCatalog:
        ...

    def resolver(self, *, as_of: datetime | None = None) -> MarketResolver:
        ...

    def lifecycle_events(self) -> tuple[LifecycleEvent, ...]:
        ...

    def runtime_components(self) -> tuple[RuntimeViewPublisher, ...]:
        ...


@dataclass(frozen=True, slots=True)
class ReferenceCatalogSummaryView:
    entity_count: int = 0
    asset_count: int = 0
    instrument_count: int = 0
    listing_count: int = 0
    market_count: int = 0
    lifecycle_event_count: int = 0


class ReferenceCatalogProjection:
    key = "reference.catalog"
    schema = ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("entity_count", "known reference entity count", "runtime state", "reference service"),
            ViewFieldSchema("asset_count", "known reference asset count", "runtime state", "reference service"),
            ViewFieldSchema("instrument_count", "known reference instrument count", "runtime state", "reference service"),
            ViewFieldSchema("listing_count", "known reference listing count", "runtime state", "reference service"),
            ViewFieldSchema("market_count", "known reference market count", "runtime state", "reference service"),
            ViewFieldSchema("lifecycle_event_count", "known reference lifecycle event count", "runtime state", "reference service"),
        ),
        mutability="runtime_writable",
        evidence="runtime reference catalog projection",
    )

    def __init__(self, service: ReferenceService) -> None:
        self.service = service

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def view(self) -> ReferenceCatalogSummaryView:
        catalog = self.service.catalog()
        return ReferenceCatalogSummaryView(
            entity_count=len(catalog.entities()),
            asset_count=len(catalog.assets()),
            instrument_count=len(catalog.instruments()),
            listing_count=len(catalog.listings()),
            market_count=len(catalog.markets()),
            lifecycle_event_count=len(self.service.lifecycle_events()),
        )


@dataclass(frozen=True, slots=True)
class ReferenceServiceProjectionProvider:
    service: ReferenceService

    def runtime_components(self) -> tuple[RuntimeViewPublisher, ...]:
        return (ReferenceCatalogProjection(self.service),)


__all__ = [
    "ReferenceCatalogProjection",
    "ReferenceCatalogSummaryView",
    "ReferenceService",
    "ReferenceServiceProjectionProvider",
]
