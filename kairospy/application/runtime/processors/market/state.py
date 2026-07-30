from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.application.protocol import RuntimeEnvelope
from kairospy.core.market import MarketViewKeys, market_window_schema
from kairospy.core.views import ViewSchema, ViewStore

from .projection import MarketProjectionState


@dataclass(frozen=True, slots=True)
class MarketViewState:
    projection: MarketProjectionState

    @property
    def schemas(self) -> tuple[ViewSchema, ...]:
        return self.projection.schemas

    def on_event(self, event: RuntimeEnvelope) -> None:
        self.projection.apply_envelope(event)

    def publish(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        views.put_runtime(MarketViewKeys.subscriptions, self.projection.subscriptions_view(), as_of=as_of, available_time=as_of)
        for key, kind, window in self.projection.window_views():
            if views.registry.get(key) is None:
                views.register(market_window_schema(key, kind))
            views.put_runtime(key, window, as_of=as_of, available_time=as_of)
        views.put_runtime(MarketViewKeys.windows, self.projection.windows_view(), as_of=as_of, available_time=as_of)


__all__ = ["MarketViewState"]
