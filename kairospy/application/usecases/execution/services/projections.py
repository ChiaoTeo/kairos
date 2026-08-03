from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.domain.execution import (
    ExecutionCurrentView,
    ExecutionFillSummary,
    ExecutionFillsView,
    ExecutionOrderSummary,
)
from kairospy.domain.order import OrderState


@dataclass(frozen=True, slots=True)
class ExecutionProjectionService:
    coordinator: object
    fills_source: object | None = None

    def order_states(self) -> tuple[OrderState, ...]:
        return tuple(self.coordinator.orders.states)

    def pending_reservation_count(self) -> int:
        return sum(
            1
            for reservation in self.coordinator.reservations.reservations
            if reservation.status.value in {"held", "reflected"}
        )

    def ledger_event_count(self) -> int:
        return len(self.coordinator.ledger.events)

    def fills(self) -> tuple[object, ...]:
        return tuple(getattr(self.fills_source, "fills", ()) or ())

    def market_id_for_order(self, order_id: str) -> str | None:
        if not order_id:
            return None
        for order in self.coordinator.orders.states:
            if order.order_id == order_id:
                return _optional_text(order.request.market_id)
        return None

    def current_view(self) -> ExecutionCurrentView:
        orders = tuple(_execution_order_summary(order) for order in self.order_states())
        active = tuple(item for item in orders if item.status not in {"filled", "canceled", "rejected", "expired"})
        return ExecutionCurrentView(
            total_orders=len(orders),
            active_orders=len(active),
            terminal_orders=len(orders) - len(active),
            unknown_orders=sum(1 for item in orders if item.status == "unknown"),
            pending_reservations=self.pending_reservation_count(),
            ledger_event_count=self.ledger_event_count(),
            latest_order=_latest_order(orders),
            orders=orders,
        )

    def fills_view(self) -> ExecutionFillsView:
        fills = tuple(_fill_summary(fill, market_id=self.market_id_for_order(str(getattr(fill, "order_id", "") or ""))) for fill in self.fills())
        return ExecutionFillsView(total_fills=len(fills), fills=fills)


def _execution_order_summary(order: OrderState) -> ExecutionOrderSummary:
    return ExecutionOrderSummary(
        order_id=order.order_id,
        instrument_id=order.request.instrument_id,
        status=order.status.value,
        side=order.request.side.value,
        quantity=order.request.quantity,
        filled_quantity=order.filled_quantity,
        remaining_quantity=order.remaining_quantity,
        market_id=order.request.market_id,
        order_venue_id=order.order_venue_id or order.request.order_venue_id,
        updated_at=order.updated_at,
        reason=order.reason,
    )


def _latest_order(orders: tuple[ExecutionOrderSummary, ...]) -> ExecutionOrderSummary | None:
    if not orders:
        return None
    return max(orders, key=lambda item: item.updated_at or datetime.min)


def _fill_summary(fill: object, *, market_id: str | None) -> ExecutionFillSummary:
    side = getattr(fill, "side", "")
    return ExecutionFillSummary(
        order_id=str(getattr(fill, "order_id", "") or ""),
        intent_id=_optional_text(getattr(fill, "intent_id", None)),
        instrument_id=str(getattr(fill, "instrument_id", "") or ""),
        side=str(getattr(side, "value", side) or ""),
        quantity=getattr(fill, "quantity"),
        price=getattr(fill, "price"),
        fee=getattr(fill, "fee"),
        occurred_at=getattr(fill, "occurred_at"),
        notional=getattr(fill, "notional", None),
        market_id=market_id,
    )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


__all__ = ["ExecutionProjectionService"]

