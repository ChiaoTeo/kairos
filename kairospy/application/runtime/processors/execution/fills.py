from __future__ import annotations

from kairospy.application.runtime.services import RuntimeExecutionService
from kairospy.core.execution import EXECUTION_FILLS_SCHEMA, ExecutionFillSummary, ExecutionFillsView, ExecutionViewKeys


class ExecutionFillsViewState:
    key = ExecutionViewKeys.fills
    schema = EXECUTION_FILLS_SCHEMA

    def __init__(self, service: RuntimeExecutionService) -> None:
        self.service = service

    def view(self) -> ExecutionFillsView:
        return self.service.fills_view()


__all__ = ["ExecutionFillsViewState"]
