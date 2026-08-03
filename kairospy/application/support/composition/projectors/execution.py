from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.application.usecases.execution.application.runtime import RuntimeExecutionService
from kairospy.domain.execution import ExecutionUpdate


class ExecutionUpdateParser(Protocol):
    def parse_execution_update(self, event: RuntimeEnvelope) -> ExecutionUpdate | None: ...


class ExecutionProjector:
    def __init__(self, *, service: RuntimeExecutionService, update_parser: ExecutionUpdateParser | None = None) -> None:
        self.service = service
        self.update_parser = update_parser

    def on_event(self, event: RuntimeEnvelope) -> None:
        update = self._execution_update(event)
        if update is not None:
            self.service.apply_update(update)

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        return None

    def register_views(self, views: ViewStore) -> None:
        for schema in self.service.schemas():
            if views.registry.get(schema.key) is None:
                views.register(schema)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        views.put_runtime("execution.current", self.service.current_view(), as_of=as_of, available_time=as_of)
        views.put_runtime("execution.fills", self.service.fills_view(), as_of=as_of, available_time=as_of)

    def _execution_update(self, event: RuntimeEnvelope) -> ExecutionUpdate | None:
        if str(event.domain) != "execution":
            return None
        payload = event.payload
        if isinstance(payload, ExecutionUpdate):
            return payload
        if isinstance(payload, Mapping) and isinstance(payload.get("update"), ExecutionUpdate):
            return payload["update"]
        if self.update_parser is not None:
            return self.update_parser.parse_execution_update(event)
        return None


__all__ = ["ExecutionProjector", "ExecutionUpdateParser"]
