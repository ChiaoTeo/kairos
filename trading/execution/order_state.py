from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from trading.adapters.base import ComboOrderRequest, OrderAck, OrderRequest


DurableOrderRequest = OrderRequest | ComboOrderRequest


class DurableOrderStatus(StrEnum):
    PLANNED = "planned"
    APPROVED = "approved"
    SUBMITTING = "submitting"
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    @property
    def terminal(self) -> bool:
        return self in {
            DurableOrderStatus.REJECTED,
            DurableOrderStatus.FILLED,
            DurableOrderStatus.CANCELLED,
            DurableOrderStatus.EXPIRED,
        }


ALLOWED_ORDER_TRANSITIONS: dict[DurableOrderStatus, frozenset[DurableOrderStatus]] = {
    DurableOrderStatus.PLANNED: frozenset({DurableOrderStatus.APPROVED, DurableOrderStatus.REJECTED}),
    DurableOrderStatus.APPROVED: frozenset({DurableOrderStatus.SUBMITTING, DurableOrderStatus.REJECTED}),
    DurableOrderStatus.SUBMITTING: frozenset({
        DurableOrderStatus.ACKNOWLEDGED,
        DurableOrderStatus.REJECTED,
        DurableOrderStatus.UNKNOWN,
    }),
    DurableOrderStatus.UNKNOWN: frozenset({
        DurableOrderStatus.ACKNOWLEDGED,
        DurableOrderStatus.REJECTED,
        DurableOrderStatus.PARTIALLY_FILLED,
        DurableOrderStatus.FILLED,
        DurableOrderStatus.CANCELLED,
        DurableOrderStatus.EXPIRED,
    }),
    DurableOrderStatus.ACKNOWLEDGED: frozenset({
        DurableOrderStatus.PARTIALLY_FILLED,
        DurableOrderStatus.FILLED,
        DurableOrderStatus.CANCELLING,
        DurableOrderStatus.CANCELLED,
        DurableOrderStatus.EXPIRED,
        DurableOrderStatus.UNKNOWN,
    }),
    DurableOrderStatus.PARTIALLY_FILLED: frozenset({
        DurableOrderStatus.PARTIALLY_FILLED,
        DurableOrderStatus.FILLED,
        DurableOrderStatus.CANCELLING,
        DurableOrderStatus.CANCELLED,
        DurableOrderStatus.EXPIRED,
        DurableOrderStatus.UNKNOWN,
    }),
    DurableOrderStatus.CANCELLING: frozenset({
        DurableOrderStatus.CANCELLED,
        DurableOrderStatus.PARTIALLY_FILLED,
        DurableOrderStatus.FILLED,
        DurableOrderStatus.UNKNOWN,
        DurableOrderStatus.EXPIRED,
    }),
    DurableOrderStatus.REJECTED: frozenset(),
    DurableOrderStatus.FILLED: frozenset(),
    DurableOrderStatus.CANCELLED: frozenset(),
    DurableOrderStatus.EXPIRED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class DurableOrderRecord:
    request: DurableOrderRequest
    status: DurableOrderStatus
    created_at: datetime
    updated_at: datetime
    ack: OrderAck | None = None
    reason: str | None = None


def require_order_transition(current: DurableOrderStatus, target: DurableOrderStatus) -> None:
    if target not in ALLOWED_ORDER_TRANSITIONS[current]:
        raise ValueError(f"illegal durable order transition: {current.value} -> {target.value}")
