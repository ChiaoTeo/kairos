from __future__ import annotations

from datetime import datetime

from kairospy.application.runtime.ports import MarketDataPort
from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.views import ViewStore

from .state import MarketViewState
from .projection import MarketProjectionState


class MarketProcessor:
    def __init__(self, port: MarketDataPort) -> None:
        self.state = MarketViewState(MarketProjectionState(data=port))

    def on_event(self, event: RuntimeEnvelope) -> None:
        self.state.on_event(event)

    def register_views(self, views: ViewStore) -> None:
        for schema in self.state.schemas:
            if views.registry.get(schema.key) is None:
                views.register(schema)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        self.state.publish(views, as_of=as_of)


__all__ = ["MarketProcessor"]
