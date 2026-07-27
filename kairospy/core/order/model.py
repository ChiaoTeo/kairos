from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from kairospy.core.account.model import AccountContext


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def position_sign(self) -> int:
        return 1 if self is OrderSide.BUY else -1


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class OrderOrigin(StrEnum):
    SYSTEM = "system"
    VENUE = "venue"
    MANUAL = "manual"
    IMPORTED = "imported"
    UNKNOWN = "unknown"


class OrderStatus(StrEnum):
    PLANNED = "planned"
    RESERVED = "reserved"
    SUBMITTING = "submitting"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"

    @property
    def terminal(self) -> bool:
        return self in {self.FILLED, self.CANCELED, self.REJECTED, self.EXPIRED}


class OrderEventKind(StrEnum):
    PLANNED = "planned"
    RESERVED = "reserved"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELED = "canceled"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class OrderIdentity:
    local_order_id: str
    origin: OrderOrigin = OrderOrigin.SYSTEM
    client_order_id: str | None = None
    venue_order_id: str | None = None

    def __post_init__(self) -> None:
        if not self.local_order_id.strip():
            raise ValueError("local_order_id cannot be empty")
        if self.client_order_id is not None and not self.client_order_id.strip():
            raise ValueError("client_order_id cannot be blank")
        if self.venue_order_id is not None and not self.venue_order_id.strip():
            raise ValueError("venue_order_id cannot be blank")
        if self.origin is OrderOrigin.SYSTEM and self.client_order_id is None:
            raise ValueError("system orders require client_order_id")
        if self.origin is not OrderOrigin.SYSTEM and self.client_order_id == self.local_order_id:
            raise ValueError("external order local_order_id must not masquerade as client_order_id")

    @classmethod
    def system(cls, client_order_id: str, *, venue_order_id: str | None = None) -> "OrderIdentity":
        return cls(client_order_id, OrderOrigin.SYSTEM, client_order_id, venue_order_id)

    @classmethod
    def external(
        cls,
        *,
        broker: str,
        account_id: str,
        venue_order_id: str,
        segment: str = "",
        origin: OrderOrigin = OrderOrigin.VENUE,
    ) -> "OrderIdentity":
        if origin is OrderOrigin.SYSTEM:
            raise ValueError("external identity cannot use system origin")
        segment_part = f":{segment}" if segment else ""
        local_order_id = f"external:{broker}:{account_id}{segment_part}:{venue_order_id}"
        return cls(local_order_id, origin, None, venue_order_id)


@dataclass(frozen=True, slots=True)
class OrderRequest:
    client_order_id: str
    context: AccountContext
    instrument_id: str
    side: OrderSide
    quantity: Decimal
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None
    market_id: str | None = None
    reservation_id: str | None = None
    origin: OrderOrigin = OrderOrigin.SYSTEM
    venue_order_id: str | None = None

    def __post_init__(self) -> None:
        if not self.client_order_id.strip() or not self.instrument_id.strip():
            raise ValueError("order request identity fields cannot be empty")
        if self.market_id is not None and not self.market_id.strip():
            raise ValueError("order request market_id cannot be blank")
        if self.origin is not OrderOrigin.SYSTEM and self.venue_order_id is None:
            raise ValueError("external order requests require venue_order_id")
        if self.quantity <= 0:
            raise ValueError("order quantity must be positive")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit order requires limit_price")
        if self.limit_price is not None and self.limit_price <= 0:
            raise ValueError("limit_price must be positive")

    @property
    def identity(self) -> OrderIdentity:
        if self.origin is OrderOrigin.SYSTEM:
            return OrderIdentity.system(self.client_order_id, venue_order_id=self.venue_order_id)
        return OrderIdentity(
            self.client_order_id,
            self.origin,
            None,
            self.venue_order_id,
        )

    @property
    def local_order_id(self) -> str:
        return self.identity.local_order_id

    @classmethod
    def external(
        cls,
        *,
        context: AccountContext,
        venue_order_id: str,
        instrument_id: str,
        side: OrderSide,
        quantity: Decimal,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Decimal | None = None,
        origin: OrderOrigin = OrderOrigin.VENUE,
        market_id: str | None = None,
    ) -> "OrderRequest":
        identity = OrderIdentity.external(
            broker=context.account.broker,
            account_id=context.account.account_id,
            segment=context.account.segment,
            venue_order_id=venue_order_id,
            origin=origin,
        )
        return cls(
            identity.local_order_id,
            context,
            instrument_id,
            side,
            quantity,
            order_type,
            limit_price,
            market_id,
            origin=origin,
            venue_order_id=venue_order_id,
        )


@dataclass(frozen=True, slots=True)
class OrderEvent:
    client_order_id: str
    kind: OrderEventKind
    occurred_at: datetime
    venue_order_id: str | None = None
    filled_quantity: Decimal | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.client_order_id.strip():
            raise ValueError("order event client_order_id cannot be empty")
        if self.occurred_at.tzinfo is None:
            raise ValueError("order event timestamp must be timezone-aware")
        if self.filled_quantity is not None and self.filled_quantity <= 0:
            raise ValueError("filled quantity must be positive")


@dataclass(frozen=True, slots=True)
class OrderState:
    request: OrderRequest
    status: OrderStatus = OrderStatus.PLANNED
    venue_order_id: str | None = None
    filled_quantity: Decimal = Decimal("0")
    updated_at: datetime | None = None
    reason: str = ""

    @property
    def identity(self) -> OrderIdentity:
        venue_order_id = self.venue_order_id or self.request.venue_order_id
        if self.request.origin is OrderOrigin.SYSTEM:
            return OrderIdentity.system(self.request.client_order_id, venue_order_id=venue_order_id)
        return OrderIdentity(
            self.request.client_order_id,
            self.request.origin,
            None,
            venue_order_id,
        )

    @property
    def local_order_id(self) -> str:
        return self.identity.local_order_id

    @property
    def remaining_quantity(self) -> Decimal:
        remaining = self.request.quantity - self.filled_quantity
        return max(remaining, Decimal("0"))

    def apply(self, event: OrderEvent) -> "OrderState":
        if event.client_order_id != self.request.client_order_id:
            raise ValueError("order event does not belong to this order")
        next_status = _next_status(self.status, event.kind)
        filled = self.filled_quantity
        if event.filled_quantity is not None:
            if event.kind is OrderEventKind.PARTIALLY_FILLED:
                filled = event.filled_quantity
            elif event.kind is OrderEventKind.FILLED:
                filled = event.filled_quantity
        if filled < self.filled_quantity or filled > self.request.quantity:
            raise ValueError("invalid cumulative filled quantity")
        if next_status is OrderStatus.PARTIALLY_FILLED and filled >= self.request.quantity:
            next_status = OrderStatus.FILLED
        if next_status is OrderStatus.FILLED and filled == 0:
            filled = self.request.quantity
        return replace(
            self,
            status=next_status,
            venue_order_id=event.venue_order_id or self.venue_order_id,
            filled_quantity=filled,
            updated_at=event.occurred_at,
            reason=event.reason,
        )


def _next_status(status: OrderStatus, kind: OrderEventKind) -> OrderStatus:
    transitions = {
        OrderStatus.PLANNED: {
            OrderEventKind.RESERVED: OrderStatus.RESERVED,
            OrderEventKind.SUBMITTED: OrderStatus.SUBMITTING,
            OrderEventKind.REJECTED: OrderStatus.REJECTED,
            OrderEventKind.UNKNOWN: OrderStatus.UNKNOWN,
        },
        OrderStatus.RESERVED: {
            OrderEventKind.SUBMITTED: OrderStatus.SUBMITTING,
            OrderEventKind.REJECTED: OrderStatus.REJECTED,
            OrderEventKind.CANCELED: OrderStatus.CANCELED,
            OrderEventKind.UNKNOWN: OrderStatus.UNKNOWN,
        },
        OrderStatus.SUBMITTING: {
            OrderEventKind.ACKNOWLEDGED: OrderStatus.ACKNOWLEDGED,
            OrderEventKind.REJECTED: OrderStatus.REJECTED,
            OrderEventKind.PARTIALLY_FILLED: OrderStatus.PARTIALLY_FILLED,
            OrderEventKind.FILLED: OrderStatus.FILLED,
            OrderEventKind.UNKNOWN: OrderStatus.UNKNOWN,
        },
        OrderStatus.ACKNOWLEDGED: {
            OrderEventKind.PARTIALLY_FILLED: OrderStatus.PARTIALLY_FILLED,
            OrderEventKind.FILLED: OrderStatus.FILLED,
            OrderEventKind.CANCEL_REQUESTED: OrderStatus.CANCEL_REQUESTED,
            OrderEventKind.CANCELED: OrderStatus.CANCELED,
            OrderEventKind.EXPIRED: OrderStatus.EXPIRED,
            OrderEventKind.UNKNOWN: OrderStatus.UNKNOWN,
        },
        OrderStatus.PARTIALLY_FILLED: {
            OrderEventKind.PARTIALLY_FILLED: OrderStatus.PARTIALLY_FILLED,
            OrderEventKind.FILLED: OrderStatus.FILLED,
            OrderEventKind.CANCEL_REQUESTED: OrderStatus.CANCEL_REQUESTED,
            OrderEventKind.CANCELED: OrderStatus.CANCELED,
            OrderEventKind.EXPIRED: OrderStatus.EXPIRED,
            OrderEventKind.UNKNOWN: OrderStatus.UNKNOWN,
        },
        OrderStatus.CANCEL_REQUESTED: {
            OrderEventKind.PARTIALLY_FILLED: OrderStatus.PARTIALLY_FILLED,
            OrderEventKind.FILLED: OrderStatus.FILLED,
            OrderEventKind.CANCELED: OrderStatus.CANCELED,
            OrderEventKind.UNKNOWN: OrderStatus.UNKNOWN,
        },
        OrderStatus.UNKNOWN: {
            OrderEventKind.ACKNOWLEDGED: OrderStatus.ACKNOWLEDGED,
            OrderEventKind.PARTIALLY_FILLED: OrderStatus.PARTIALLY_FILLED,
            OrderEventKind.FILLED: OrderStatus.FILLED,
            OrderEventKind.CANCELED: OrderStatus.CANCELED,
            OrderEventKind.REJECTED: OrderStatus.REJECTED,
            OrderEventKind.EXPIRED: OrderStatus.EXPIRED,
        },
    }
    try:
        return transitions[status][kind]
    except KeyError as error:
        raise ValueError(f"illegal order transition: {status} + {kind}") from error


__all__ = [
    "OrderEvent",
    "OrderEventKind",
    "OrderIdentity",
    "OrderOrigin",
    "OrderRequest",
    "OrderSide",
    "OrderState",
    "OrderStatus",
    "OrderType",
]
