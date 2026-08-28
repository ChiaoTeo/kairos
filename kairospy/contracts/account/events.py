"""Account-owned classified event contract."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, cast

from kairospy.infrastructure.protocol.eventing import EventMetadataRead
from kairospy.primitives.account import AccountIdRead, SegmentKeyRead

if TYPE_CHECKING:
    from kairospy._native_account_contract import (
        AccountBalanceEvent,
        AccountBalanceRemovedEvent as AccountBalanceRemovedData,
        AccountEarnHoldingEvent,
        AccountEarnHoldingRemovedEvent as AccountEarnHoldingRemovedData,
        AccountEvent,
        AccountEventProvenance,
        AccountObservedOrderEvent,
        AccountObservedOrderRemovedEvent as AccountObservedOrderRemovedData,
        AccountPositionEvent,
        AccountPositionRemovedEvent as AccountPositionRemovedData,
        AccountStatusEvent,
        AccountValuationEvent,
    )


class _AccountEventBase(Protocol):
    metadata: EventMetadataRead
    account_id: AccountIdRead
    segment_key: SegmentKeyRead
    provenance: AccountEventProvenance | None


class AccountBalanceUpsertedEvent(_AccountEventBase, Protocol):
    kind: Literal["balance_upserted"]
    data: AccountBalanceEvent


class AccountBalanceRemovedEvent(_AccountEventBase, Protocol):
    kind: Literal["balance_removed"]
    data: AccountBalanceRemovedData


class AccountPositionUpsertedEvent(_AccountEventBase, Protocol):
    kind: Literal["position_upserted"]
    data: AccountPositionEvent


class AccountPositionRemovedEvent(_AccountEventBase, Protocol):
    kind: Literal["position_removed"]
    data: AccountPositionRemovedData


class AccountEarnHoldingUpsertedEvent(_AccountEventBase, Protocol):
    kind: Literal["earn_holding_upserted"]
    data: AccountEarnHoldingEvent


class AccountEarnHoldingRemovedEvent(_AccountEventBase, Protocol):
    kind: Literal["earn_holding_removed"]
    data: AccountEarnHoldingRemovedData


class AccountValuationChangedEvent(_AccountEventBase, Protocol):
    kind: Literal["valuation_changed"]
    data: AccountValuationEvent


class AccountStatusChangedEvent(_AccountEventBase, Protocol):
    kind: Literal["account_status_changed"]
    data: AccountStatusEvent


class AccountObservedOrderUpsertedEvent(_AccountEventBase, Protocol):
    kind: Literal["observed_order_upserted"]
    data: AccountObservedOrderEvent


class AccountObservedOrderRemovedEvent(_AccountEventBase, Protocol):
    kind: Literal["observed_order_removed"]
    data: AccountObservedOrderRemovedData


AccountEventVariant: TypeAlias = (
    AccountBalanceUpsertedEvent
    | AccountBalanceRemovedEvent
    | AccountPositionUpsertedEvent
    | AccountPositionRemovedEvent
    | AccountEarnHoldingUpsertedEvent
    | AccountEarnHoldingRemovedEvent
    | AccountValuationChangedEvent
    | AccountStatusChangedEvent
    | AccountObservedOrderUpsertedEvent
    | AccountObservedOrderRemovedEvent
)


def decode_event(frame: bytes) -> AccountEventVariant:
    return cast(AccountEventVariant, import_module("kairospy._native_account_contract").decode_event(frame))


if not TYPE_CHECKING:
    AccountEvent = import_module("kairospy._native_account_contract").AccountEvent


__all__ = [
    "AccountBalanceRemovedEvent",
    "AccountBalanceUpsertedEvent",
    "AccountEarnHoldingRemovedEvent",
    "AccountEarnHoldingUpsertedEvent",
    "AccountEvent",
    "AccountEventVariant",
    "AccountObservedOrderRemovedEvent",
    "AccountObservedOrderUpsertedEvent",
    "AccountPositionRemovedEvent",
    "AccountPositionUpsertedEvent",
    "AccountStatusChangedEvent",
    "AccountValuationChangedEvent",
    "decode_event",
]
