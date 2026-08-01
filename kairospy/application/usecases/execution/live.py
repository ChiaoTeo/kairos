from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from kairospy.core.account import AccountBookRef
from kairospy.core.order import OrderSide, OrderType


@dataclass(frozen=True, slots=True)
class OrderSubmissionRequest:
    account: AccountBookRef
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None
    integration_options: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class OrderSubmissionResult:
    order_venue_id: str
    status: str = ""


@dataclass(frozen=True, slots=True)
class OrderCancelRequest:
    account: AccountBookRef
    order_venue_id: str
    symbol: str | None = None
    integration_options: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class OrderCancelResult:
    order_venue_id: str
    status: str = ""


class OrderExecutionPort(Protocol):
    def submit(self, request: OrderSubmissionRequest) -> OrderSubmissionResult:
        ...

    def cancel(self, request: OrderCancelRequest) -> OrderCancelResult:
        ...


__all__ = [
    "OrderCancelRequest",
    "OrderCancelResult",
    "OrderExecutionPort",
    "OrderSubmissionRequest",
    "OrderSubmissionResult",
]
