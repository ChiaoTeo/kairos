from __future__ import annotations

from datetime import datetime

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.views import ViewStore

from .current import ExecutionCurrentViewState


class ExecutionProcessor:
    def __init__(self, coordinator: ExecutionCoordinator) -> None:
        self.state = ExecutionCurrentViewState(coordinator)

    def on_event(self, event: RuntimeEnvelope) -> None:
        self.state.on_event(event)

    def register_views(self, views: ViewStore) -> None:
        if views.registry.get(self.state.schema.key) is None:
            views.register(self.state.schema)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        views.put_runtime(self.state.key, self.state.view(), as_of=as_of, available_time=as_of)


__all__ = ["ExecutionProcessor"]
