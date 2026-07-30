from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from kairospy.core.views import ViewFieldSchema, ViewSchema


class ExecutionViewKeys:
    current = "execution.current"
    fills = "execution.fills"


@dataclass(frozen=True, slots=True)
class ExecutionOrderSummary:
    order_id: str
    instrument_id: str
    status: str
    side: str
    quantity: Decimal
    filled_quantity: Decimal
    remaining_quantity: Decimal
    market_id: str | None = None
    order_venue_id: str | None = None
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


@dataclass(frozen=True, slots=True)
class ExecutionFillSummary:
    order_id: str
    intent_id: str | None
    instrument_id: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    occurred_at: datetime
    notional: Decimal | None = None
    market_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionFillsView:
    total_fills: int = 0
    fills: tuple[ExecutionFillSummary, ...] = ()


EXECUTION_CURRENT_SCHEMA = ViewSchema(
    ExecutionViewKeys.current,
    "system",
    fields=(
        ViewFieldSchema("total_orders", "known order count", "runtime state", "execution projection service"),
        ViewFieldSchema("active_orders", "non-terminal order count", "runtime state", "order journal"),
        ViewFieldSchema("terminal_orders", "terminal order count", "runtime state", "order journal"),
        ViewFieldSchema("unknown_orders", "orders requiring reconciliation", "runtime state", "order journal"),
        ViewFieldSchema("pending_reservations", "open local reservations", "runtime state", "reservation book"),
        ViewFieldSchema("ledger_event_count", "local account ledger event count", "runtime state", "account ledger"),
        ViewFieldSchema("latest_order", "most recently updated order", "order update time", "order journal"),
        ViewFieldSchema("orders", "local order state summaries", "runtime state", "order journal"),
    ),
    mutability="runtime_writable",
    evidence="runtime execution projection service view state",
)

EXECUTION_FILLS_SCHEMA = ViewSchema(
    ExecutionViewKeys.fills,
    "system",
    fields=(
        ViewFieldSchema("total_fills", "known execution fill count", "runtime state", "execution service"),
        ViewFieldSchema("fills", "execution fill summaries", "fill event time", "execution service"),
    ),
    mutability="runtime_writable",
    evidence="runtime execution fill view state",
)


__all__ = [
    "EXECUTION_CURRENT_SCHEMA",
    "EXECUTION_FILLS_SCHEMA",
    "ExecutionCurrentView",
    "ExecutionFillSummary",
    "ExecutionFillsView",
    "ExecutionOrderSummary",
    "ExecutionViewKeys",
]
