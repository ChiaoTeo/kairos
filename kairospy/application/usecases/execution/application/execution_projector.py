from __future__ import annotations

from datetime import datetime

from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.usecases.execution.application.runtime import RuntimeExecutionService


class ExecutionProjector:
    def __init__(self, *, service: RuntimeExecutionService) -> None:
        self.service = service

    def on_event(self, event: object) -> None:
        # Execution updates are applied by AccountActor before the system
        # topology is projected.  This projector is read-only.
        return None

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        return None

    def register_views(self, views: ViewStore) -> None:
        for schema in self.service.schemas():
            if views.registry.get(schema.key) is None:
                views.register(schema)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        views.put_runtime("execution.current", self.service.current_view(), as_of=as_of, available_time=as_of)
        views.put_runtime("execution.fills", self.service.fills_view(), as_of=as_of, available_time=as_of)

__all__ = ["ExecutionProjector"]
