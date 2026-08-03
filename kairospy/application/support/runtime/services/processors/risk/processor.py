from __future__ import annotations

from datetime import datetime

from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.application.support.runtime.application.views import ViewStore

from .events import RiskEventViewState


class RiskProcessor:
    def __init__(self) -> None:
        self.state = RiskEventViewState()

    def on_event(self, event: RuntimeEnvelope) -> None:
        self.state.on_event(event)

    def register_views(self, views: ViewStore) -> None:
        if views.registry.get(self.state.schema.key) is None:
            views.register(self.state.schema)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        views.put_runtime(self.state.key, self.state.view(), as_of=as_of, available_time=as_of)


__all__ = ["RiskProcessor"]
