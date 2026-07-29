from __future__ import annotations

from datetime import datetime

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.views import ViewStore

from .events import SystemEventViewState
from .runtime import RuntimeSystemViewState


class SystemProcessor:
    def __init__(self, *, strategy_id: str) -> None:
        self.runtime = RuntimeSystemViewState(strategy_id=strategy_id)
        self.events = SystemEventViewState()
        self.states = (self.runtime, self.events)

    def on_event(self, event: RuntimeEnvelope) -> None:
        for state in self.states:
            state.on_event(event)

    def register_views(self, views: ViewStore) -> None:
        for state in self.states:
            if views.registry.get(state.schema.key) is None:
                views.register(state.schema)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        for state in self.states:
            views.put_runtime(state.key, state.view(), as_of=as_of, available_time=as_of)


__all__ = ["SystemProcessor"]
