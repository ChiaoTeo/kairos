from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from kairospy.identity import InstrumentId
from kairospy.execution.events import TradeSide
from kairospy.reference.contracts import ProductType


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(StrEnum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class MarginMode(StrEnum):
    NONE = "none"
    SECURITIES = "securities"
    CROSS = "cross"
    ISOLATED = "isolated"


class PositionMode(StrEnum):
    ONE_WAY = "one_way"
    HEDGE = "hedge"


class TriggerPriceSource(StrEnum):
    LAST = "last"
    MARK = "mark"
    INDEX = "index"


class SelfTradePrevention(StrEnum):
    NONE = "none"
    EXPIRE_TAKER = "expire_taker"
    EXPIRE_MAKER = "expire_maker"
    EXPIRE_BOTH = "expire_both"


@dataclass(frozen=True, slots=True)
class ExecutionInstructions:
    order_type: OrderType
    time_in_force: TimeInForce
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    post_only: bool = False
    reduce_only: bool = False
    margin_mode: MarginMode = MarginMode.NONE
    leverage: Decimal | None = None
    position_mode: PositionMode = PositionMode.ONE_WAY
    trigger_price_source: TriggerPriceSource = TriggerPriceSource.LAST
    iceberg_quantity: Decimal | None = None
    self_trade_prevention: SelfTradePrevention = SelfTradePrevention.NONE

    def __post_init__(self) -> None:
        if self.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT} and self.limit_price is None:
            raise ValueError("limit order requires limit price")
        if self.order_type in {OrderType.STOP, OrderType.STOP_LIMIT} and self.stop_price is None:
            raise ValueError("stop order requires stop price")
        if self.post_only and self.order_type is not OrderType.LIMIT:
            raise ValueError("post-only requires limit order")
        if self.leverage is not None and self.leverage <= 0:
            raise ValueError("leverage must be positive")


class OrderStatus(StrEnum):
    CREATED = "created"
    WORKING = "working"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REJECTED = "rejected"

    @property
    def terminal(self) -> bool:
        return self in {self.FILLED, self.CANCELLED, self.EXPIRED, self.REJECTED}


@dataclass(frozen=True, slots=True)
class OrderLeg:
    instrument_id: InstrumentId
    side: TradeSide
    ratio: int = 1

    def __post_init__(self) -> None:
        if self.ratio < 1:
            raise ValueError("order leg ratio must be positive")


@dataclass(frozen=True, slots=True)
class Order:
    order_id: UUID
    intent_id: UUID
    strategy_id: str
    structure_id: UUID
    legs: tuple[OrderLeg, ...]
    quantity: int
    limit_price: Decimal | None
    time_in_force: TimeInForce
    created_at: datetime
    eligible_at: datetime
    expires_at: datetime
    is_closing: bool
    status: OrderStatus = OrderStatus.CREATED
    filled_quantity: int = 0
    reason: str | None = None

    def transition(self, status: OrderStatus, *, filled_quantity: int | None = None, reason: str | None = None) -> "Order":
        allowed = {
            OrderStatus.CREATED: {OrderStatus.WORKING, OrderStatus.REJECTED, OrderStatus.CANCELLED},
            OrderStatus.WORKING: {
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.FILLED,
                OrderStatus.EXPIRED,
                OrderStatus.CANCELLED,
                OrderStatus.REJECTED,
            },
            OrderStatus.PARTIALLY_FILLED: {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED},
        }
        if self.status.terminal or status not in allowed.get(self.status, set()):
            raise ValueError(f"illegal order transition: {self.status} -> {status}")
        filled = self.filled_quantity if filled_quantity is None else filled_quantity
        if not 0 <= filled <= self.quantity:
            raise ValueError("invalid filled quantity")
        if status is OrderStatus.FILLED and filled != self.quantity:
            raise ValueError("filled order must have full quantity")
        return replace(self, status=status, filled_quantity=filled, reason=reason)


@dataclass(frozen=True, slots=True)
class ExecutionCapabilities:
    order_types: frozenset[OrderType]
    product_types: frozenset[ProductType]
    supports_combo_orders: bool = False
    supports_reduce_only: bool = False
    supports_post_only: bool = False
    margin_modes: frozenset[MarginMode] = frozenset({MarginMode.NONE})
    position_modes: frozenset[PositionMode] = frozenset({PositionMode.ONE_WAY})

    def require_order_type(self, order_type: OrderType) -> None:
        if order_type not in self.order_types:
            raise ValueError(f"execution gateway does not support order type: {order_type}")

    def require_product(self, product_type: ProductType) -> None:
        if product_type not in self.product_types:
            raise ValueError(f"execution gateway does not support product: {product_type}")
