from __future__ import annotations

from datetime import datetime

from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.support.messaging import Message
from kairospy.application.usecases.execution.application.runtime import RuntimeExecutionService
from kairospy.domain.order import ORDER_CURRENT_SCHEMA, OrderCurrentView, OrderViewKeys


class OrderProjector:
    key = OrderViewKeys.current
    schema = ORDER_CURRENT_SCHEMA

    def __init__(self, service: RuntimeExecutionService) -> None:
        self.service = service

    def on_event(self, event: Message) -> None:
        return None

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        return None

    def register_views(self, views: ViewStore) -> None:
        if views.registry.get(self.schema.key) is None:
            views.register(self.schema)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        views.put_runtime(self.key, OrderCurrentView(self.service.current_view()), as_of=as_of, available_time=as_of)


__all__ = ["OrderProjector"]
