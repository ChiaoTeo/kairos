from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from kairospy.application.support.runtime.events import RuntimeEnvelope
from kairospy.application.support.runtime.services.application import RuntimeExecutionService
from kairospy.core.execution import ExecutionUpdate
from kairospy.core.views import ViewStore

from .current import ExecutionCurrentViewState
from .fills import ExecutionFillsViewState

class ExecutionUpdateParser(Protocol):
    def __call__(self, event: RuntimeEnvelope) -> ExecutionUpdate | None:
        ...


class ExecutionProcessor:
    def __init__(
        self,
        *,
        service: RuntimeExecutionService,
        update_parser: ExecutionUpdateParser | None = None,
    ) -> None:
        self.update_parser = update_parser
        self.service = service
        self.state = ExecutionCurrentViewState(service)
        self.fills = ExecutionFillsViewState(service)

    def on_event(self, event: RuntimeEnvelope) -> None:
        update = self._execution_update(event)
        if update is not None:
            self.service.apply_update(update)
        self.state.on_event(event)

    def register_views(self, views: ViewStore) -> None:
        if views.registry.get(self.state.schema.key) is None:
            views.register(self.state.schema)
        if views.registry.get(self.fills.schema.key) is None:
            views.register(self.fills.schema)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        views.put_runtime(self.state.key, self.state.view(), as_of=as_of, available_time=as_of)
        views.put_runtime(self.fills.key, self.fills.view(), as_of=as_of, available_time=as_of)

    def _execution_update(self, event: RuntimeEnvelope) -> ExecutionUpdate | None:
        if str(event.domain) != "execution":
            return None
        payload = event.payload
        if isinstance(payload, ExecutionUpdate):
            return payload
        if isinstance(payload, Mapping) and isinstance(payload.get("update"), ExecutionUpdate):
            return payload["update"]
        if self.update_parser is not None:
            return self.update_parser(event)
        return None


__all__ = ["ExecutionProcessor", "ExecutionUpdateParser"]
