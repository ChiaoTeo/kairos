from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import uuid4

from kairospy.domain.reference import AccountId, InstrumentId, IntentId, MarketId, StrategyId


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


@runtime_checkable
class Intent(Protocol):
    intent_id: IntentId | str
    strategy_id: StrategyId | str
    kind: object
    created_at: datetime | None
    reason: str


@dataclass(frozen=True, slots=True)
class TradeIntent(Intent):
    intent_id: IntentId | str
    strategy_id: StrategyId | str
    instrument_id: InstrumentId | str
    kind: IntentKind
    market_id: MarketId | str | None = None
    account_id: AccountId | str | None = None
    account_index: int | None = None
    account_book: str | None = None
    created_at: datetime | None = None
    target_quantity: Decimal | None = None
    quantity: Decimal | None = None
    limit_price: Decimal | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_id", _id(self.intent_id, IntentId, "intent_id"))
        object.__setattr__(self, "strategy_id", _id(self.strategy_id, StrategyId, "strategy_id"))
        object.__setattr__(self, "instrument_id", _id(self.instrument_id, InstrumentId, "instrument_id"))
        object.__setattr__(self, "market_id", None if self.market_id is None else _id(self.market_id, MarketId, "market_id"))
        object.__setattr__(self, "account_id", None if self.account_id is None else _id(self.account_id, AccountId, "account_id"))
        if self.account_index is not None and self.account_index < 0:
            raise ValueError("intent account_index cannot be negative")
        object.__setattr__(self, "account_book", None if self.account_book is None else _required_text(self.account_book, "account_book"))
        if self.quantity is not None and self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.limit_price is not None and self.limit_price <= 0:
            raise ValueError("limit_price must be positive")
        if self.created_at is not None and self.created_at.tzinfo is None:
            raise ValueError("intent timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class IntentEvent:
    intent_id: IntentId | str
    kind: IntentEventKind
    occurred_at: datetime
    order_ids: tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_id", _id(self.intent_id, IntentId, "intent_id"))
        if self.occurred_at.tzinfo is None:
            raise ValueError("intent event timestamp must be timezone-aware")
        object.__setattr__(self, "order_ids", tuple(str(item) for item in self.order_ids))


@dataclass(frozen=True, slots=True)
class IntentState:
    intent: Intent
    status: IntentStatus = IntentStatus.CREATED
    order_ids: tuple[str, ...] = ()
    updated_at: datetime | None = None
    reason: str = ""

    @property
    def active(self) -> bool:
        return self.status.active

    def apply(self, event: IntentEvent) -> "IntentState":
        if str(event.intent_id) != str(self.intent.intent_id):
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
    strategy_id: StrategyId | str,
    instrument_id: InstrumentId | str,
    market_id: MarketId | str | None = None,
    account_id: AccountId | str | None = None,
    account_index: int | None = None,
    account_book: object | None = None,
    target_quantity: Decimal,
    at: datetime | None = None,
    limit_price: Decimal | None = None,
    reason: str = "",
    intent_id: IntentId | str | None = None,
) -> TradeIntent:
    return TradeIntent(
        intent_id=intent_id or f"intent-{uuid4()}",
        strategy_id=strategy_id,
        instrument_id=instrument_id,
        kind=IntentKind.TARGET_POSITION,
        market_id=market_id,
        account_id=account_id,
        account_index=account_index,
        account_book=None if account_book is None else str(account_book),
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
            IntentEventKind.SATISFIED: IntentStatus.SATISFIED,
            IntentEventKind.REJECTED: IntentStatus.REJECTED,
            IntentEventKind.CANCELED: IntentStatus.CANCELED,
            IntentEventKind.FAILED: IntentStatus.FAILED,
        },
        IntentStatus.PLANNED: {
            IntentEventKind.ORDERING: IntentStatus.ORDERING,
            IntentEventKind.SATISFIED: IntentStatus.SATISFIED,
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
    "Intent",
    "IntentEvent",
    "IntentEventKind",
    "IntentKind",
    "IntentState",
    "IntentStatus",
    "TradeIntent",
    "target_position_intent",
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
