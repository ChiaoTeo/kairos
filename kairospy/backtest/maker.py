from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from kairospy.domain.execution import TradeSide
from kairospy.domain.identity import InstrumentId
from kairospy.execution.policy import ExecutionMode, ExecutionPolicy, PartialFillPolicy


class BookEventType(StrEnum):
    ADD = "add"
    CANCEL = "cancel"
    TRADE = "trade"


@dataclass(frozen=True, slots=True)
class IncrementalBookEvent:
    sequence: int
    event_time: datetime
    instrument_id: InstrumentId
    side: TradeSide
    price: Decimal
    quantity: Decimal
    event_type: BookEventType

    def __post_init__(self):
        if self.sequence < 0 or self.event_time.tzinfo is None or self.price <= 0 or self.quantity <= 0:
            raise ValueError("invalid incremental book event")


@dataclass(frozen=True, slots=True)
class MakerOrderState:
    order_id: UUID
    instrument_id: InstrumentId
    side: TradeSide
    price: Decimal
    quantity: Decimal
    submitted_at: datetime
    eligible_at: datetime
    queue_ahead: Decimal
    filled_quantity: Decimal = Decimal("0")
    last_sequence: int = -1

    @property
    def remaining(self): return self.quantity-self.filled_quantity


@dataclass(frozen=True, slots=True)
class MakerEventResult:
    order: MakerOrderState
    fill_quantity: Decimal
    reason: str


class FifoMakerFillModel:
    """Conservative visible-depth FIFO model driven by sequenced events."""

    def apply(self, order: MakerOrderState, event: IncrementalBookEvent) -> MakerEventResult:
        if event.instrument_id!=order.instrument_id or event.price!=order.price:
            return MakerEventResult(order,Decimal("0"),"unrelated_event")
        if event.event_time<order.eligible_at:return MakerEventResult(order,Decimal("0"),"before_order_ack")
        if event.sequence<=order.last_sequence:raise ValueError("book event sequence must increase")
        updated=replace(order,last_sequence=event.sequence)
        # Opposite-side events cannot consume a resting order on this side.
        if event.side is not order.side:return MakerEventResult(updated,Decimal("0"),"opposite_side_event")
        if event.event_type is BookEventType.ADD:
            return MakerEventResult(updated,Decimal("0"),"added_behind")
        if event.event_type is BookEventType.CANCEL:
            return MakerEventResult(replace(updated,queue_ahead=max(Decimal("0"),order.queue_ahead-event.quantity)),Decimal("0"),"queue_ahead_cancelled")
        consumed_ahead=min(order.queue_ahead,event.quantity);available=max(Decimal("0"),event.quantity-consumed_ahead)
        fill=min(order.remaining,available)
        return MakerEventResult(replace(updated,queue_ahead=order.queue_ahead-consumed_ahead,
            filled_quantity=order.filled_quantity+fill),fill,"filled" if fill else "queue_ahead_traded")


class HybridAction(StrEnum):
    WAIT = "wait"
    CANCEL = "cancel"
    CROSS_REMAINDER = "cross_remainder"
    HEDGE_IMMEDIATELY = "hedge_immediately"


@dataclass(frozen=True, slots=True)
class HybridDecision:
    action: HybridAction
    remaining_quantity: Decimal
    reason: str


class HybridExecutionStateMachine:
    def decide(self, order: MakerOrderState, now: datetime, policy: ExecutionPolicy) -> HybridDecision:
        if policy.mode is not ExecutionMode.HYBRID:raise ValueError("hybrid state machine requires hybrid policy")
        if now.tzinfo is None:raise ValueError("decision time must be timezone-aware")
        deadline=order.submitted_at+timedelta(milliseconds=policy.maker_timeout_ms or 0)
        if order.remaining<=0:return HybridDecision(HybridAction.CANCEL,Decimal("0"),"fully filled")
        if now<deadline:return HybridDecision(HybridAction.WAIT,order.remaining,"maker timeout not reached")
        action={
            PartialFillPolicy.HEDGE_IMMEDIATELY:HybridAction.HEDGE_IMMEDIATELY,
            PartialFillPolicy.CROSS_REMAINDER:HybridAction.CROSS_REMAINDER,
            PartialFillPolicy.CANCEL_REMAINDER:HybridAction.CANCEL,
            PartialFillPolicy.WAIT:HybridAction.WAIT,
        }[policy.partial_fill_policy]
        return HybridDecision(action,order.remaining,"maker timeout reached")


@dataclass(frozen=True, slots=True)
class ExecutionMarkout:
    fill_price: Decimal
    mark_price: Decimal
    side: TradeSide
    elapsed_seconds: int

    @property
    def adverse_selection(self) -> Decimal:
        return Decimal(self.side.sign)*(self.fill_price-self.mark_price)
