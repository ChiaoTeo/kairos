from __future__ import annotations

from datetime import datetime, timezone

from kairospy.application.protocol import RuntimeEnvelope
from kairospy.application.runtime.services import RuntimeReferenceService
from kairospy.core.reference import (
    REFERENCE_CATALOG_SCHEMA,
    REFERENCE_LIFECYCLE_EVENTS_SCHEMA,
    REFERENCE_MARKETS_SCHEMA,
    ReferenceViewKeys,
    reference_catalog_view,
    reference_lifecycle_events_view,
    reference_market_schema,
    reference_market_view,
    reference_markets_view,
)
from kairospy.core.views import ViewStore


class ReferenceCatalogViewState:
    key = ReferenceViewKeys.catalog
    schema = REFERENCE_CATALOG_SCHEMA
    schemas = (REFERENCE_CATALOG_SCHEMA, REFERENCE_MARKETS_SCHEMA, REFERENCE_LIFECYCLE_EVENTS_SCHEMA)

    def __init__(self, service: RuntimeReferenceService) -> None:
        self.service = service

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def register_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        for schema in self.schemas:
            if views.registry.get(schema.key) is None:
                views.register(schema)
        catalog = self.service.catalog()
        for market in catalog.list_markets(at=_as_of(as_of)):
            key = ReferenceViewKeys.market(reference_market_view(catalog, market, as_of=_as_of(as_of)).ref.market_key)
            if views.registry.get(key) is None:
                views.register(reference_market_schema(key))

    def publish(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        publish_as_of = _as_of(as_of)
        catalog = self.service.catalog()
        events = self.service.lifecycle_events()
        self.register_views(views, as_of=publish_as_of)
        views.put_runtime(
            ReferenceViewKeys.catalog,
            reference_catalog_view(catalog, lifecycle_events=events, as_of=publish_as_of),
            as_of=publish_as_of,
            available_time=publish_as_of,
        )
        views.put_runtime(
            ReferenceViewKeys.markets,
            reference_markets_view(catalog, as_of=publish_as_of),
            as_of=publish_as_of,
            available_time=publish_as_of,
        )
        views.put_runtime(
            ReferenceViewKeys.lifecycle_events,
            reference_lifecycle_events_view(events),
            as_of=publish_as_of,
            available_time=publish_as_of,
        )
        for market in catalog.list_markets(at=publish_as_of):
            resolved = reference_market_view(catalog, market, as_of=publish_as_of)
            views.put_runtime(
                ReferenceViewKeys.market(resolved.ref.market_key),
                resolved,
                as_of=publish_as_of,
                available_time=publish_as_of,
            )


def _as_of(value: datetime | None) -> datetime:
    return datetime.now(timezone.utc) if value is None else value


__all__ = ["ReferenceCatalogViewState"]
