from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.runtime.ports import ReferencePort
from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.views import ViewFieldSchema, ViewSchema


@dataclass(frozen=True, slots=True)
class ReferenceCatalogSummaryView:
    entity_count: int = 0
    asset_count: int = 0
    instrument_count: int = 0
    listing_count: int = 0
    market_count: int = 0
    lifecycle_event_count: int = 0


class ReferenceCatalogViewState:
    key = "reference.catalog"
    schema = ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("entity_count", "known reference entity count", "runtime state", "reference port"),
            ViewFieldSchema("asset_count", "known reference asset count", "runtime state", "reference port"),
            ViewFieldSchema("instrument_count", "known reference instrument count", "runtime state", "reference port"),
            ViewFieldSchema("listing_count", "known reference listing count", "runtime state", "reference port"),
            ViewFieldSchema("market_count", "known reference market count", "runtime state", "reference port"),
            ViewFieldSchema("lifecycle_event_count", "known reference lifecycle event count", "runtime state", "reference port"),
        ),
        mutability="runtime_writable",
        evidence="runtime reference catalog view state",
    )

    def __init__(self, port: ReferencePort) -> None:
        self.port = port

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def view(self) -> ReferenceCatalogSummaryView:
        catalog = self.port.catalog()
        return ReferenceCatalogSummaryView(
            entity_count=len(catalog.entities()),
            asset_count=len(catalog.assets()),
            instrument_count=len(catalog.instruments()),
            listing_count=len(catalog.listings()),
            market_count=len(catalog.markets()),
            lifecycle_event_count=len(self.port.lifecycle_events()),
        )


__all__ = ["ReferenceCatalogViewState", "ReferenceCatalogSummaryView"]
