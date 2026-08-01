from __future__ import annotations

from kairospy.application.support.runtime.processors.execution import ExecutionCurrentViewState
from kairospy.application.support.runtime.events import RuntimeEnvelope
from kairospy.application.support.runtime.services.application import RuntimeExecutionService
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
