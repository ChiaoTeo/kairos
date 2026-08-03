from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from kairospy.domain.reference import (
    REFERENCE_CATALOG_SCHEMA,
    REFERENCE_LIFECYCLE_EVENTS_SCHEMA,
    REFERENCE_MARKETS_SCHEMA,
    LifecycleEvent,
    ReferenceCatalog,
    ReferenceViewKeys,
    reference_catalog_view,
    reference_lifecycle_events_view,
    reference_market_schema,
    reference_market_view,
    reference_markets_view,
)
from kairospy.domain.views import ViewSchema


class ReferenceViewRegistry(Protocol):
    def get(self, key: str) -> ViewSchema | None:
        ...


class ReferenceViewStore(Protocol):
    registry: ReferenceViewRegistry

    def register(self, schema: ViewSchema) -> ViewSchema:
        ...

    def put_runtime(
        self,
        key: str,
        payload: object,
        *,
        as_of: datetime | None = None,
        available_time: datetime | None = None,
    ) -> object:
        ...


@dataclass(frozen=True, slots=True)
class ReferenceProjectionService:
    catalog: ReferenceCatalog
    lifecycle_events: tuple[LifecycleEvent, ...] = ()

    @property
    def schemas(self):
        return (REFERENCE_CATALOG_SCHEMA, REFERENCE_MARKETS_SCHEMA, REFERENCE_LIFECYCLE_EVENTS_SCHEMA)

    def register_views(self, views: ReferenceViewStore, *, as_of: datetime | None = None) -> None:
        publish_as_of = _as_of(as_of)
        for schema in self.schemas:
            if views.registry.get(schema.key) is None:
                views.register(schema)
        for market in self.catalog.list_markets(at=publish_as_of):
            key = ReferenceViewKeys.market(reference_market_view(self.catalog, market, as_of=publish_as_of).ref.market_key)
            if views.registry.get(key) is None:
                views.register(reference_market_schema(key))

    def publish_views(self, views: ReferenceViewStore, *, as_of: datetime | None = None) -> None:
        publish_as_of = _as_of(as_of)
        self.register_views(views, as_of=publish_as_of)
        views.put_runtime(
            ReferenceViewKeys.catalog,
            reference_catalog_view(self.catalog, lifecycle_events=self.lifecycle_events, as_of=publish_as_of),
            as_of=publish_as_of,
            available_time=publish_as_of,
        )
        views.put_runtime(
            ReferenceViewKeys.markets,
            reference_markets_view(self.catalog, as_of=publish_as_of),
            as_of=publish_as_of,
            available_time=publish_as_of,
        )
        views.put_runtime(
            ReferenceViewKeys.lifecycle_events,
            reference_lifecycle_events_view(self.lifecycle_events),
            as_of=publish_as_of,
            available_time=publish_as_of,
        )
        for market in self.catalog.list_markets(at=publish_as_of):
            resolved = reference_market_view(self.catalog, market, as_of=publish_as_of)
            views.put_runtime(
                ReferenceViewKeys.market(resolved.ref.market_key),
                resolved,
                as_of=publish_as_of,
                available_time=publish_as_of,
            )


def _as_of(value: datetime | None) -> datetime:
    return datetime.now(timezone.utc) if value is None else value


__all__ = ["ReferenceProjectionService"]
