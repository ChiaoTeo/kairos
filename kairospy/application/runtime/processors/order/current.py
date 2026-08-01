from __future__ import annotations

from kairospy.application.runtime.processors.execution import ExecutionCurrentViewState
from kairospy.application.protocol import RuntimeEnvelope
from kairospy.application.runtime.services import RuntimeExecutionService
from kairospy.core.order import ORDER_CURRENT_SCHEMA, OrderCurrentView, OrderViewKeys


class OrderCurrentViewState:
    key = OrderViewKeys.current
    schema = ORDER_CURRENT_SCHEMA

    def __init__(self, service: RuntimeExecutionService) -> None:
        self.state = ExecutionCurrentViewState(service)

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def view(self) -> OrderCurrentView:
        return OrderCurrentView(self.state.view())


__all__ = ["OrderCurrentViewState"]
