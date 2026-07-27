from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4


class IntentKind(StrEnum):
    TARGET_POSITION = "target_position"
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    EXIT_POSITION = "exit_position"
    REDUCE_POSITION = "reduce_position"


class IntentStatus(StrEnum):
    CREATED = "created"
    ACCEPTED = "accepted"
    PLANNED = "planned"
    ORDERING = "ordering"
    PARTIALLY_FILLED = "partially_filled"
    SATISFIED = "satisfied"
    REJECTED = "rejected"
    CANCELED = "canceled"
    EXPIRED = "expired"
    FAILED = "failed"

    @property
    def active(self) -> bool:
        return self in {
            self.CREATED,
            self.ACCEPTED,
            self.PLANNED,
            self.ORDERING,
            self.PARTIALLY_FILLED,
        }

    @property
    def terminal(self) -> bool:
        return not self.active


class IntentEventKind(StrEnum):
    CREATED = "created"
    ACCEPTED = "accepted"
    PLANNED = "planned"
    ORDERING = "ordering"
    PARTIALLY_FILLED = "partially_filled"
    SATISFIED = "satisfied"
    REJECTED = "rejected"
    CANCELED = "canceled"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class TradeIntent:
    intent_id: str
    strategy_id: str
    instrument_id: str
    kind: IntentKind
    market_id: str | None = None
    created_at: datetime | None = None
    target_quantity: Decimal | None = None
    quantity: Decimal | None = None
    limit_price: Decimal | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.intent_id.strip() or not self.strategy_id.strip() or not self.instrument_id.strip():
            raise ValueError("intent identity fields cannot be empty")
        if self.market_id is not None and not self.market_id.strip():
            raise ValueError("intent market_id cannot be blank")
        if self.target_quantity is not None and self.target_quantity < 0:
            raise ValueError("target_quantity cannot be negative")
        if self.quantity is not None and self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.limit_price is not None and self.limit_price <= 0:
            raise ValueError("limit_price must be positive")
        if self.created_at is not None and self.created_at.tzinfo is None:
            raise ValueError("intent timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class IntentEvent:
    intent_id: str
    kind: IntentEventKind
    occurred_at: datetime
    order_ids: tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.intent_id.strip():
            raise ValueError("intent event intent_id cannot be empty")
        if self.occurred_at.tzinfo is None:
            raise ValueError("intent event timestamp must be timezone-aware")
        object.__setattr__(self, "order_ids", tuple(str(item) for item in self.order_ids))


@dataclass(frozen=True, slots=True)
class IntentState:
    intent: TradeIntent
    status: IntentStatus = IntentStatus.CREATED
    order_ids: tuple[str, ...] = ()
    updated_at: datetime | None = None
    reason: str = ""

    @property
    def active(self) -> bool:
        return self.status.active

    def apply(self, event: IntentEvent) -> "IntentState":
        if event.intent_id != self.intent.intent_id:
            raise ValueError("intent event does not belong to this intent")
        return replace(
            self,
            status=_next_status(self.status, event.kind),
            order_ids=tuple(dict.fromkeys((*self.order_ids, *event.order_ids))),
            updated_at=event.occurred_at,
            reason=event.reason,
        )


def target_position_intent(
    *,
    strategy_id: str,
    instrument_id: str,
    market_id: str | None = None,
    target_quantity: Decimal,
    at: datetime | None = None,
    limit_price: Decimal | None = None,
    reason: str = "",
    intent_id: str | None = None,
) -> TradeIntent:
    return TradeIntent(
        intent_id=intent_id or f"intent-{uuid4()}",
        strategy_id=strategy_id,
        instrument_id=instrument_id,
        kind=IntentKind.TARGET_POSITION,
        market_id=market_id,
        created_at=at,
        target_quantity=target_quantity,
        limit_price=limit_price,
        reason=reason,
    )


def _next_status(status: IntentStatus, kind: IntentEventKind) -> IntentStatus:
    transitions = {
        IntentStatus.CREATED: {
            IntentEventKind.ACCEPTED: IntentStatus.ACCEPTED,
            IntentEventKind.PLANNED: IntentStatus.PLANNED,
            IntentEventKind.REJECTED: IntentStatus.REJECTED,
            IntentEventKind.CANCELED: IntentStatus.CANCELED,
            IntentEventKind.FAILED: IntentStatus.FAILED,
        },
        IntentStatus.ACCEPTED: {
            IntentEventKind.PLANNED: IntentStatus.PLANNED,
            IntentEventKind.REJECTED: IntentStatus.REJECTED,
            IntentEventKind.CANCELED: IntentStatus.CANCELED,
            IntentEventKind.FAILED: IntentStatus.FAILED,
        },
        IntentStatus.PLANNED: {
            IntentEventKind.ORDERING: IntentStatus.ORDERING,
            IntentEventKind.REJECTED: IntentStatus.REJECTED,
            IntentEventKind.CANCELED: IntentStatus.CANCELED,
            IntentEventKind.FAILED: IntentStatus.FAILED,
        },
        IntentStatus.ORDERING: {
            IntentEventKind.PARTIALLY_FILLED: IntentStatus.PARTIALLY_FILLED,
            IntentEventKind.SATISFIED: IntentStatus.SATISFIED,
            IntentEventKind.REJECTED: IntentStatus.REJECTED,
            IntentEventKind.CANCELED: IntentStatus.CANCELED,
            IntentEventKind.EXPIRED: IntentStatus.EXPIRED,
            IntentEventKind.FAILED: IntentStatus.FAILED,
        },
        IntentStatus.PARTIALLY_FILLED: {
            IntentEventKind.PARTIALLY_FILLED: IntentStatus.PARTIALLY_FILLED,
            IntentEventKind.SATISFIED: IntentStatus.SATISFIED,
            IntentEventKind.CANCELED: IntentStatus.CANCELED,
            IntentEventKind.EXPIRED: IntentStatus.EXPIRED,
            IntentEventKind.FAILED: IntentStatus.FAILED,
        },
    }
    if status is IntentStatus.CREATED and kind is IntentEventKind.CREATED:
        return IntentStatus.CREATED
    try:
        return transitions[status][kind]
    except KeyError as error:
        raise ValueError(f"illegal intent transition: {status} + {kind}") from error


__all__ = [
    "IntentEvent",
    "IntentEventKind",
    "IntentKind",
    "IntentState",
    "IntentStatus",
    "TradeIntent",
    "target_position_intent",
]
