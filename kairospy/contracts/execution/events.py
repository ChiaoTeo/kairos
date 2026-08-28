"""Execution-owned classified event contract."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, cast

from kairospy.infrastructure.protocol.eventing import EventMetadataRead
from kairospy.primitives.account import AccountIdRead
from kairospy.primitives.runtime import StrategyIdRead

if TYPE_CHECKING:
    from kairospy._native_execution_contract import (
        ExecutionEvent,
        ExecutionFillValue,
        ExecutionIntentUpdate,
        ExecutionOrderUpdate,
        ExecutionPlanCreatedValue,
    )


class _ExecutionEventBase(Protocol):
    metadata: EventMetadataRead
    strategy_id: StrategyIdRead | None
    account_id: AccountIdRead | None


class ExecutionIntentAcceptedEvent(_ExecutionEventBase, Protocol):
    kind: Literal["intent_accepted"]
    data: ExecutionIntentUpdate


class ExecutionIntentRejectedEvent(_ExecutionEventBase, Protocol):
    kind: Literal["intent_rejected"]
    data: ExecutionIntentUpdate


class ExecutionIntentLifecycleChangedEvent(_ExecutionEventBase, Protocol):
    kind: Literal["intent_lifecycle_changed"]
    data: ExecutionIntentUpdate


class ExecutionPlanCreatedEvent(_ExecutionEventBase, Protocol):
    kind: Literal["plan_created"]
    data: ExecutionPlanCreatedValue


class ExecutionOrderSubmittedEvent(_ExecutionEventBase, Protocol):
    kind: Literal["order_submitted"]
    data: ExecutionOrderUpdate


class ExecutionOrderAcceptedEvent(_ExecutionEventBase, Protocol):
    kind: Literal["order_accepted"]
    data: ExecutionOrderUpdate


class ExecutionOrderRejectedEvent(_ExecutionEventBase, Protocol):
    kind: Literal["order_rejected"]
    data: ExecutionOrderUpdate


class ExecutionOrderCanceledEvent(_ExecutionEventBase, Protocol):
    kind: Literal["order_canceled"]
    data: ExecutionOrderUpdate


class ExecutionOrderExpiredEvent(_ExecutionEventBase, Protocol):
    kind: Literal["order_expired"]
    data: ExecutionOrderUpdate


class ExecutionFillRecordedEvent(_ExecutionEventBase, Protocol):
    kind: Literal["fill_recorded"]
    data: ExecutionFillValue


ExecutionEventVariant: TypeAlias = (
    ExecutionIntentAcceptedEvent
    | ExecutionIntentRejectedEvent
    | ExecutionIntentLifecycleChangedEvent
    | ExecutionPlanCreatedEvent
    | ExecutionOrderSubmittedEvent
    | ExecutionOrderAcceptedEvent
    | ExecutionOrderRejectedEvent
    | ExecutionOrderCanceledEvent
    | ExecutionOrderExpiredEvent
    | ExecutionFillRecordedEvent
)


def decode_event(frame: bytes) -> ExecutionEventVariant:
    return cast(
        ExecutionEventVariant,
        import_module("kairospy._native_execution_contract").decode_event(frame),
    )


if not TYPE_CHECKING:
    ExecutionEvent = import_module("kairospy._native_execution_contract").ExecutionEvent


__all__ = [
    "ExecutionEvent",
    "ExecutionEventVariant",
    "ExecutionFillRecordedEvent",
    "ExecutionIntentAcceptedEvent",
    "ExecutionIntentLifecycleChangedEvent",
    "ExecutionIntentRejectedEvent",
    "ExecutionOrderAcceptedEvent",
    "ExecutionOrderCanceledEvent",
    "ExecutionOrderExpiredEvent",
    "ExecutionOrderRejectedEvent",
    "ExecutionOrderSubmittedEvent",
    "ExecutionPlanCreatedEvent",
    "decode_event",
]
