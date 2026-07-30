from __future__ import annotations

from kairospy.application.protocol import RuntimeEnvelope
from kairospy.application.service.runtime import RuntimeExecutionService
from kairospy.core.execution import EXECUTION_CURRENT_SCHEMA, ExecutionCurrentView, ExecutionOrderSummary, ExecutionViewKeys


class ExecutionCurrentViewState:
    key = ExecutionViewKeys.current
    schema = EXECUTION_CURRENT_SCHEMA

    def __init__(self, service: RuntimeExecutionService) -> None:
        self.service = service

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def view(self) -> ExecutionCurrentView:
        return self.service.current_view()


__all__ = ["ExecutionCurrentViewState"]
