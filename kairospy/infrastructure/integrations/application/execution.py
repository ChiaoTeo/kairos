from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from kairospy.domain.account import AccountBookRef
from kairospy.domain.order import OrderSide, OrderType
from kairospy.infrastructure.integrations.application.connections import IntegrationConnection


@dataclass(frozen=True, slots=True)
class ConnectionOrderOptions:
    time_in_force: str | None = None
    reduce_only: bool | None = None
    post_only: bool | None = None


@dataclass(frozen=True, slots=True)
class ConnectionOrderSubmissionRequest:
    account: AccountBookRef
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None
    options: ConnectionOrderOptions | None = None
    client_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectionOrderSubmissionResult:
    order_venue_id: str
    status: str = ""


@dataclass(frozen=True, slots=True)
class ConnectionOrderCancelRequest:
    account: AccountBookRef
    order_venue_id: str
    symbol: str | None = None
    options: ConnectionOrderOptions | None = None


@dataclass(frozen=True, slots=True)
class ConnectionOrderCancelResult:
    order_venue_id: str
    status: str = ""


class OrderConnection(IntegrationConnection, Protocol):
    def submit(self, request: ConnectionOrderSubmissionRequest) -> ConnectionOrderSubmissionResult: ...
    def cancel(self, request: ConnectionOrderCancelRequest) -> ConnectionOrderCancelResult: ...


__all__ = [
    "ConnectionOrderOptions",
    "ConnectionOrderSubmissionRequest",
    "ConnectionOrderSubmissionResult",
    "ConnectionOrderCancelRequest",
    "ConnectionOrderCancelResult",
    "OrderConnection",
]
