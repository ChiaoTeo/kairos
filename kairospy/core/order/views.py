from __future__ import annotations

from dataclasses import dataclass

from kairospy.core.execution import ExecutionCurrentView
from kairospy.core.views import ViewFieldSchema, ViewSchema


class OrderViewKeys:
    current = "order.current"


@dataclass(frozen=True, slots=True)
class OrderCurrentView:
    state: ExecutionCurrentView


ORDER_CURRENT_SCHEMA = ViewSchema(
    OrderViewKeys.current,
    "system",
    fields=(ViewFieldSchema("state", "order state exposed from execution projection service", "runtime state", "execution.current"),),
    mutability="runtime_writable",
    evidence="runtime order view state backed by execution projection service",
)


__all__ = ["ORDER_CURRENT_SCHEMA", "OrderCurrentView", "OrderViewKeys"]
