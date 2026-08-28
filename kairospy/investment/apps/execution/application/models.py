from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TypeAlias

from kairospy.investment.apps.reference.application import InstrumentRef
from kairospy.primitives.account import AccountId, SegmentKey
from kairospy.primitives.execution import FillId, IntentId, OrderId
from kairospy.primitives.reference import InstrumentId
from kairospy.primitives.decimal import (
    DecimalValue,
    Money,
    MoneyLike,
    Price,
    PriceLike,
    Quantity,
    QuantityLike,
)


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class TimeInForce(StrEnum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class SubmissionStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PENDING = "pending"
    DUPLICATE = "duplicate"


class DeliveryCertainty(StrEnum):
    NOT_SENT = "not_sent"
    SENT = "sent"
    INDETERMINATE = "indeterminate"


class CommitmentStatus(StrEnum):
    HELD_BEFORE_SEND = "held_before_send"
    ACTIVE = "active"
    UNCERTAIN = "uncertain"
    REDUCED = "reduced"
    RELEASED = "released"
    RECONCILED = "reconciled"


class RiskReservationSagaStatus(StrEnum):
    AUTHORIZE_PENDING = "authorize_pending"
    ACTIVE = "active"
    RESIZE_PENDING = "resize_pending"
    RELEASE_PENDING = "release_pending"
    CONSUME_PENDING = "consume_pending"
    RELEASED = "released"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    UNCERTAIN = "uncertain"
    FAILED = "failed"


class IntentStatus(StrEnum):
    ACCEPTED = "accepted"
    PLANNING = "planning"
    PLANNED = "planned"
    EXECUTING = "executing"
    PARTIALLY_FILLED = "partially_filled"
    CANCEL_REQUESTED = "cancel_requested"
    SATISFIED = "satisfied"
    PENDING = "pending"
    ACTIVE = "active"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"
    COMPENSATING = "compensating"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    UNKNOWN = "unknown"


class OrderStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    SUBMITTING = "submitting"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class MarketOrderRequest:
    instrument: InstrumentRef | InstrumentId
    account: AccountId | str
    side: OrderSide
    quantity: Quantity
    time_in_force: TimeInForce = TimeInForce.IOC
    reduce_only: bool = False
    reason: str = ""
    request_id: str | None = None
    segment: SegmentKey | str = "spot"

    def __post_init__(self) -> None:
        quantity = Quantity.positive(self.quantity)
        object.__setattr__(self, "quantity", quantity)
        if not str(self.segment).strip():
            raise ValueError("order segment is required")


@dataclass(frozen=True, slots=True)
class LimitOrderRequest:
    instrument: InstrumentRef | InstrumentId
    account: AccountId | str
    side: OrderSide
    quantity: Quantity
    limit_price: Price
    time_in_force: TimeInForce = TimeInForce.DAY
    post_only: bool = False
    reduce_only: bool = False
    reason: str = ""
    request_id: str | None = None
    segment: SegmentKey | str = "spot"

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", Quantity.positive(self.quantity))
        object.__setattr__(self, "limit_price", Price(self.limit_price))
        if not str(self.segment).strip():
            raise ValueError("order segment is required")


OrderRequest: TypeAlias = MarketOrderRequest | LimitOrderRequest


@dataclass(frozen=True, slots=True)
class ReplaceOrderRequest:
    quantity: Quantity | None = None
    limit_price: Price | None = None
    time_in_force: TimeInForce | None = None
    reason: str = ""
    request_id: str | None = None

    def __post_init__(self) -> None:
        if self.quantity is not None:
            object.__setattr__(self, "quantity", Quantity.positive(self.quantity))
        if self.limit_price is not None:
            object.__setattr__(self, "limit_price", Price(self.limit_price))
        if (
            self.quantity is None
            and self.limit_price is None
            and self.time_in_force is None
        ):
            raise ValueError(
                "replacement must change quantity, price, or time_in_force"
            )


@dataclass(frozen=True, slots=True)
class IntentReceipt:
    request_id: str
    intent_id: IntentId | None
    status: SubmissionStatus
    delivery_certainty: DeliveryCertainty
    error: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status in {SubmissionStatus.ACCEPTED, SubmissionStatus.DUPLICATE}

    @property
    def may_have_been_sent(self) -> bool:
        return self.delivery_certainty is not DeliveryCertainty.NOT_SENT

    def require_accepted(self) -> IntentReceipt:
        if not self.accepted:
            raise RuntimeError(
                self.error or f"Execution request {self.request_id} was rejected"
            )
        return self


@dataclass(frozen=True, slots=True)
class OrderCommandReceipt:
    request_id: str
    order_id: OrderId | None
    intent_id: IntentId | None
    status: SubmissionStatus
    delivery_certainty: DeliveryCertainty
    error: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status in {SubmissionStatus.ACCEPTED, SubmissionStatus.DUPLICATE}

    @property
    def may_have_been_sent(self) -> bool:
        return self.delivery_certainty is not DeliveryCertainty.NOT_SENT

    def require_accepted(self) -> OrderCommandReceipt:
        if not self.accepted:
            raise RuntimeError(
                self.error or f"Execution request {self.request_id} was rejected"
            )
        return self


@dataclass(frozen=True, slots=True)
class BulkOrderCommandReceipt:
    request_id: str
    order_ids: tuple[OrderId, ...]
    status: SubmissionStatus
    delivery_certainty: DeliveryCertainty
    error: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status in {SubmissionStatus.ACCEPTED, SubmissionStatus.DUPLICATE}

    @property
    def may_have_been_sent(self) -> bool:
        return self.delivery_certainty is not DeliveryCertainty.NOT_SENT

    def require_accepted(self) -> BulkOrderCommandReceipt:
        if not self.accepted:
            raise RuntimeError(
                self.error or f"Execution request {self.request_id} was rejected"
            )
        return self


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    id: IntentId
    strategy_id: str
    instrument: InstrumentRef
    account_ids: tuple[AccountId, ...]
    target_quantity: QuantityLike | None
    status: IntentStatus
    reason: str
    order_ids: tuple[OrderId, ...]
    source_event_sequence: int | None = None
    strategy_decision_id: str | None = None
    updated_at_unix_nanos: int | None = None

    def __post_init__(self) -> None:
        if self.target_quantity is not None:
            object.__setattr__(
                self, "target_quantity", _quantity(self.target_quantity)
            )


@dataclass(frozen=True, slots=True)
class Order:
    id: OrderId
    strategy_id: str
    intent_id: IntentId | None
    instrument: InstrumentRef
    account_id: AccountId
    side: OrderSide
    quantity: QuantityLike
    filled_quantity: QuantityLike
    limit_price: PriceLike | None
    status: OrderStatus
    updated_at: datetime | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", _quantity(self.quantity))
        object.__setattr__(self, "filled_quantity", _quantity(self.filled_quantity))
        object.__setattr__(self, "limit_price", _optional_price(self.limit_price))


@dataclass(frozen=True, slots=True)
class OrderCommitment:
    order_id: OrderId
    account_id: AccountId
    segment_key: SegmentKey
    instrument_id: InstrumentId
    resource_kind: str
    resource_id: str
    amount: DecimalValue
    remaining_quantity: QuantityLike
    status: CommitmentStatus
    basis_kind: str
    updated_at_unix_nanos: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", _decimal_value(self.amount))
        object.__setattr__(
            self, "remaining_quantity", _quantity(self.remaining_quantity)
        )


@dataclass(frozen=True, slots=True)
class RiskReservationSaga:
    order_id: OrderId
    reservation_id: str
    idempotency_key: str
    account_id: AccountId
    amount: MoneyLike
    status: RiskReservationSagaStatus
    risk_generation: int
    risk_event_sequence: int
    policy_version: int
    expires_at_unix_nanos: int
    updated_at_unix_nanos: int
    funding_requirement: "ExecutionFundingRequirement | None" = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", _money(self.amount))


@dataclass(frozen=True, slots=True)
class ExecutionFundingRequirement:
    required_margin: MoneyLike
    available_margin: MoneyLike
    shortfall: MoneyLike
    margin_rule_id: str
    risk_decision_id: str
    risk_policy_version: int
    account_snapshot_watermark: int
    broker: str
    segment: str
    collateral_asset: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "required_margin", _money(self.required_margin))
        object.__setattr__(self, "available_margin", _money(self.available_margin))
        object.__setattr__(self, "shortfall", _money(self.shortfall))


@dataclass(frozen=True, slots=True)
class Fill:
    id: FillId
    order_id: OrderId
    instrument: InstrumentRef
    quantity: QuantityLike
    price: PriceLike
    occurred_at: datetime
    intent_id: IntentId | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", _quantity(self.quantity))
        object.__setattr__(self, "price", _price(self.price))


@dataclass(frozen=True, slots=True)
class ExecutionBacktestResult:
    fills: tuple[Fill, ...]


def _decimal_value(value: object) -> DecimalValue:
    if isinstance(value, DecimalValue):
        return value
    raise TypeError("Execution commitment amount must satisfy DecimalValue")


def _quantity(value: object) -> QuantityLike:
    if isinstance(value, QuantityLike):
        return value
    if isinstance(value, (Decimal, str, int)) and not isinstance(value, bool):
        return Quantity(value)
    raise TypeError("Execution quantity is invalid")


def _price(value: object) -> PriceLike:
    if isinstance(value, PriceLike):
        return value
    if isinstance(value, (Decimal, str, int)) and not isinstance(value, bool):
        return Price(value)
    raise TypeError("Execution price is invalid")


def _optional_price(value: object | None) -> PriceLike | None:
    return None if value is None else _price(value)


def _money(value: object) -> MoneyLike:
    if isinstance(value, MoneyLike):
        return value
    if isinstance(value, (Decimal, str, int)) and not isinstance(value, bool):
        return Money(value)
    raise TypeError("Execution money is invalid")
