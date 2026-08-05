from __future__ import annotations

from datetime import datetime

from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.support.messaging import Message
from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.domain.market import MarketViewKeys, market_window_schema


class MarketProjector:
    def __init__(self, service: MarketApplication) -> None:
        self.service = service

    def on_event(self, event: Message) -> None:
        self.service.handle_event(event)

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        return None

    def register_views(self, views: ViewStore) -> None:
        for schema in self.service.projection_schemas:
            if views.registry.get(schema.key) is None:
                views.register(schema)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        views.put_runtime(MarketViewKeys.subscriptions, self.service.subscriptions_view(), as_of=as_of, available_time=as_of)
        for key, kind, window in self.service.window_views():
            if views.registry.get(key) is None:
                views.register(market_window_schema(key, kind))
            views.put_runtime(key, window, as_of=as_of, available_time=as_of)
        views.put_runtime(MarketViewKeys.windows, self.service.windows_view(), as_of=as_of, available_time=as_of)


__all__ = ["MarketProjector"]
