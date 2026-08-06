from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from kairospy.domain.account.model import AccountRuntimeContext
from kairospy.domain.reference import AssetType, ExternalAccountId, BrokerId, InstrumentId, MarketId


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
    order_id: str
    origin: OrderOrigin = OrderOrigin.SYSTEM
    order_venue_id: str | None = None

    def __post_init__(self) -> None:
        if not self.order_id.strip():
            raise ValueError("order_id cannot be empty")
        if self.order_venue_id is not None and not self.order_venue_id.strip():
            raise ValueError("order_venue_id cannot be blank")

    @classmethod
    def system(cls, order_id: str, *, order_venue_id: str | None = None) -> "OrderIdentity":
        return cls(order_id, OrderOrigin.SYSTEM, order_venue_id)

    @classmethod
    def external(
        cls,
        *,
        broker: BrokerId | str,
        account_id: ExternalAccountId | str,
        order_venue_id: str,
        segment: str = "",
        origin: OrderOrigin = OrderOrigin.VENUE,
    ) -> "OrderIdentity":
        if origin is OrderOrigin.SYSTEM:
            raise ValueError("external identity cannot use system origin")
        segment_part = f":{segment}" if segment else ""
        order_id = f"external:{broker}:{account_id}{segment_part}:{order_venue_id}"
        return cls(order_id, origin, order_venue_id)


@dataclass(frozen=True, slots=True)
class OrderRequest:
    order_id: str
    context: AccountRuntimeContext
    instrument_id: InstrumentId | str
    side: OrderSide
    quantity: Decimal
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None
    market_id: MarketId | str | None = None
    reservation_id: str | None = None
    origin: OrderOrigin = OrderOrigin.SYSTEM
    order_venue_id: str | None = None
    asset_type: AssetType | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument_id", _id(self.instrument_id, InstrumentId, "instrument_id"))
        object.__setattr__(self, "market_id", None if self.market_id is None else _id(self.market_id, MarketId, "market_id"))
        object.__setattr__(self, "asset_type", None if self.asset_type is None else AssetType(str(self.asset_type)))
        if not self.order_id.strip():
            raise ValueError("order request identity fields cannot be empty")
        if self.origin is not OrderOrigin.SYSTEM and self.order_venue_id is None:
            raise ValueError("external order requests require order_venue_id")
        if self.quantity <= 0:
            raise ValueError("order quantity must be positive")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit order requires limit_price")
        if self.limit_price is not None and self.limit_price <= 0:
            raise ValueError("limit_price must be positive")

    @property
    def identity(self) -> OrderIdentity:
        if self.origin is OrderOrigin.SYSTEM:
            return OrderIdentity.system(self.order_id, order_venue_id=self.order_venue_id)
        return OrderIdentity(
            self.order_id,
            self.origin,
            self.order_venue_id,
        )

    @classmethod
    def external(
        cls,
        *,
        context: AccountRuntimeContext,
        order_venue_id: str,
        instrument_id: InstrumentId | str,
        side: OrderSide,
        quantity: Decimal,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Decimal | None = None,
        origin: OrderOrigin = OrderOrigin.VENUE,
        market_id: MarketId | str | None = None,
        ) -> "OrderRequest":
        scope = context.segment
        identity = OrderIdentity.external(
            broker=scope.broker,
            account_id=scope.account_id,
            segment=scope.key,
            order_venue_id=order_venue_id,
            origin=origin,
        )
        return cls(
            identity.order_id,
            context,
            instrument_id,
            side,
            quantity,
            order_type,
            limit_price,
            market_id,
            origin=origin,
            order_venue_id=order_venue_id,
        )


@dataclass(frozen=True, slots=True)
class OrderEvent:
    order_id: str
    kind: OrderEventKind
    occurred_at: datetime
    order_venue_id: str | None = None
    filled_quantity: Decimal | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.order_id.strip():
            raise ValueError("order event order_id cannot be empty")
        if self.occurred_at.tzinfo is None:
            raise ValueError("order event timestamp must be timezone-aware")
        if self.filled_quantity is not None and self.filled_quantity <= 0:
            raise ValueError("filled quantity must be positive")


@dataclass(frozen=True, slots=True)
class OrderState:
    request: OrderRequest
    status: OrderStatus = OrderStatus.PLANNED
    order_venue_id: str | None = None
    filled_quantity: Decimal = Decimal("0")
    updated_at: datetime | None = None
    reason: str = ""

    @property
    def identity(self) -> OrderIdentity:
        order_venue_id = self.order_venue_id or self.request.order_venue_id
        if self.request.origin is OrderOrigin.SYSTEM:
            return OrderIdentity.system(self.request.order_id, order_venue_id=order_venue_id)
        return OrderIdentity(
            self.request.order_id,
            self.request.origin,
            order_venue_id,
        )

    @property
    def order_id(self) -> str:
        return self.identity.order_id

    @property
    def remaining_quantity(self) -> Decimal:
        remaining = self.request.quantity - self.filled_quantity
        return max(remaining, Decimal("0"))

    def apply(self, event: OrderEvent) -> "OrderState":
        if event.order_id != self.request.order_id:
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
            order_venue_id=event.order_venue_id or self.order_venue_id,
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


def _required_text(value: object, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} cannot be empty")
    return text


def _id(value, id_type, label: str):
    if isinstance(value, id_type):
        return value
    return id_type(_required_text(value, label))
