from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.runtime.projection.execution import ExecutionCurrentProjection, ExecutionCurrentView
from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.views import ViewFieldSchema, ViewSchema


@dataclass(frozen=True, slots=True)
class OrderCurrentView:
    execution: ExecutionCurrentView


class OrderCurrentProjection:
    key = "order.current"
    schema = ViewSchema(
        key,
        "system",
        fields=(ViewFieldSchema("execution", "order state exposed from execution coordinator", "runtime state", "execution.current"),),
        mutability="runtime_writable",
        evidence="runtime order projection backed by execution coordinator",
    )

    def __init__(self, coordinator: ExecutionCoordinator) -> None:
        self.execution = ExecutionCurrentProjection(coordinator)

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def view(self) -> OrderCurrentView:
        return OrderCurrentView(self.execution.view())


__all__ = ["OrderCurrentProjection", "OrderCurrentView"]
