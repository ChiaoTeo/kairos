from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from kairospy.core.order import OrderState
from kairospy.core.views import ViewFieldSchema, ViewSchema

from .coordinator import ExecutionCoordinator


@dataclass(frozen=True, slots=True)
class ExecutionOrderSummary:
    client_order_id: str
    instrument_id: str
    status: str
    side: str
    quantity: Decimal
    filled_quantity: Decimal
    remaining_quantity: Decimal
    market_id: str | None = None
    venue_order_id: str | None = None
    updated_at: datetime | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionCurrentView:
    total_orders: int = 0
    active_orders: int = 0
    terminal_orders: int = 0
    unknown_orders: int = 0
    pending_reservations: int = 0
    ledger_event_count: int = 0
    latest_order: ExecutionOrderSummary | None = None
    orders: tuple[ExecutionOrderSummary, ...] = ()


class ExecutionCurrentProjection:
    """Projection for local execution state owned by an ExecutionCoordinator."""

    def __init__(self, coordinator: ExecutionCoordinator, *, key: str = "execution.current") -> None:
        self.coordinator = coordinator
        self.key = key
        self.schema = ViewSchema(
            self.key,
            "system",
            fields=(
                ViewFieldSchema("total_orders", "known order count", "runtime state", "execution coordinator"),
                ViewFieldSchema("active_orders", "non-terminal order count", "runtime state", "order journal"),
                ViewFieldSchema("terminal_orders", "terminal order count", "runtime state", "order journal"),
                ViewFieldSchema("unknown_orders", "orders requiring reconciliation", "runtime state", "order journal"),
                ViewFieldSchema("pending_reservations", "open local reservations", "runtime state", "reservation book"),
                ViewFieldSchema("ledger_event_count", "local account ledger event count", "runtime state", "account ledger"),
                ViewFieldSchema("latest_order", "most recently updated order", "order update time", "order journal"),
                ViewFieldSchema("orders", "local order state summaries", "runtime state", "order journal"),
            ),
            mutability="runtime_writable",
            evidence="runtime execution coordinator projection",
        )

    def on_event(self, event: object) -> None:
        return None

    def view(self) -> ExecutionCurrentView:
        orders = tuple(_execution_order_summary(order) for order in self.coordinator.orders.states)
        active = tuple(item for item in orders if item.status not in {"filled", "canceled", "rejected", "expired"})
        latest = _latest_order(orders)
        return ExecutionCurrentView(
            total_orders=len(orders),
            active_orders=len(active),
            terminal_orders=len(orders) - len(active),
            unknown_orders=sum(1 for item in orders if item.status == "unknown"),
            pending_reservations=sum(1 for item in self.coordinator.reservations.reservations if item.status.value in {"held", "reflected"}),
            ledger_event_count=len(self.coordinator.ledger.events),
            latest_order=latest,
            orders=orders,
        )


def _execution_order_summary(order: OrderState) -> ExecutionOrderSummary:
    return ExecutionOrderSummary(
        client_order_id=order.request.client_order_id,
        instrument_id=order.request.instrument_id,
        status=order.status.value,
        side=order.request.side.value,
        quantity=order.request.quantity,
        filled_quantity=order.filled_quantity,
        remaining_quantity=order.remaining_quantity,
        market_id=order.request.market_id,
        venue_order_id=order.venue_order_id or order.request.venue_order_id,
        updated_at=order.updated_at,
        reason=order.reason,
    )


def _latest_order(orders: tuple[ExecutionOrderSummary, ...]) -> ExecutionOrderSummary | None:
    if not orders:
        return None
    return max(orders, key=lambda item: item.updated_at or datetime.min)


__all__ = ["ExecutionCurrentProjection", "ExecutionCurrentView", "ExecutionOrderSummary"]
