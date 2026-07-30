from __future__ import annotations

from datetime import datetime

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.views import ViewStore

from .current import ExecutionCurrentViewState
from .fills import ExecutionFillsViewState


class ExecutionProcessor:
    def __init__(self, coordinator: ExecutionCoordinator, *, fills_source: object | None = None) -> None:
        self.state = ExecutionCurrentViewState(coordinator)
        self.fills = ExecutionFillsViewState(coordinator, fills_source=fills_source)

    def on_event(self, event: RuntimeEnvelope) -> None:
        self.state.on_event(event)

    def register_views(self, views: ViewStore) -> None:
        if views.registry.get(self.state.schema.key) is None:
            views.register(self.state.schema)
        if views.registry.get(self.fills.schema.key) is None:
            views.register(self.fills.schema)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        views.put_runtime(self.state.key, self.state.view(), as_of=as_of, available_time=as_of)
        views.put_runtime(self.fills.key, self.fills.view(), as_of=as_of, available_time=as_of)


__all__ = ["ExecutionProcessor"]
