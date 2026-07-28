from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.core.views import ViewStore

from ..model import RuntimeDataEnvelope
from .base import RuntimeComponent, RuntimeProjection
from .market import MarketProjection
from .system import RuntimeSystemProjection


@dataclass(frozen=True, slots=True)
class SystemProjectionAdapter:
    projection: RuntimeSystemProjection

    def register(self, views: ViewStore) -> None:
        return None

    def on_event(self, event: RuntimeDataEnvelope) -> None:
        return None

    def publish(
        self,
        views: ViewStore,
        *,
        as_of: datetime | None,
        event_count: int,
        runtime_event_count: int,
        last_event: RuntimeDataEnvelope | None,
        last_runtime_event: RuntimeDataEnvelope | None,
        status: str,
    ) -> datetime | None:
        return self.projection.publish(
            views,
            event_count=event_count,
            runtime_event_count=runtime_event_count,
            last_event=last_event,
            last_runtime_event=last_runtime_event,
            status=status,
        )


@dataclass(frozen=True, slots=True)
class RuntimeProjectionRegistry:
    projections: tuple[RuntimeProjection, ...]
    components: tuple[RuntimeComponent, ...] = ()

    def register(self, views: ViewStore) -> None:
        for projection in self.projections:
            projection.register(views)
        for component in self.components:
            views.register(component.schema)

    def on_event(self, event: RuntimeDataEnvelope) -> None:
        for projection in self.projections:
            projection.on_event(event)
        for component in self.components:
            component.on_event(event)

    def publish_views(
        self,
        views: ViewStore,
        *,
        event_count: int,
        runtime_event_count: int,
        last_event: RuntimeDataEnvelope | None,
        last_runtime_event: RuntimeDataEnvelope | None,
        status: str,
    ) -> datetime | None:
        as_of: datetime | None = None
        for projection in self.projections:
            as_of = projection.publish(
                views,
                as_of=as_of,
                event_count=event_count,
                runtime_event_count=runtime_event_count,
                last_event=last_event,
                last_runtime_event=last_runtime_event,
                status=status,
            )
        return as_of

    def publish_component_views(self, views: ViewStore, *, last_runtime_event: RuntimeDataEnvelope | None) -> None:
        as_of = None if last_runtime_event is None else last_runtime_event.time
        for component in self.components:
            views.put_runtime(component.key, component.view(), as_of=as_of, available_time=as_of)


__all__ = [
    "MarketProjection",
    "RuntimeProjection",
    "RuntimeProjectionRegistry",
    "SystemProjectionAdapter",
]
