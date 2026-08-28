"""Risk-owned classified event contract."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, cast

from kairospy.infrastructure.protocol.eventing import EventMetadataRead
from kairospy.primitives.account import AccountIdRead
from kairospy.primitives.runtime import StrategyIdRead

if TYPE_CHECKING:
    from kairospy._native_risk_contract import (
        RiskCircuitEventPayload,
        RiskDecisionEventPayload,
        RiskEvent,
        RiskReservationEventPayload,
    )


class _RiskEventBase(Protocol):
    metadata: EventMetadataRead
    account_id: AccountIdRead | None
    strategy_id: StrategyIdRead | None


class RiskDecisionMadeEvent(_RiskEventBase, Protocol):
    kind: Literal["decision_made"]
    data: RiskDecisionEventPayload


class RiskReservationReservedEvent(_RiskEventBase, Protocol):
    kind: Literal["reservation_reserved"]
    data: RiskReservationEventPayload


class RiskReservationConsumedEvent(_RiskEventBase, Protocol):
    kind: Literal["reservation_consumed"]
    data: RiskReservationEventPayload


class RiskReservationReleasedEvent(_RiskEventBase, Protocol):
    kind: Literal["reservation_released"]
    data: RiskReservationEventPayload


class RiskReservationExpiredEvent(_RiskEventBase, Protocol):
    kind: Literal["reservation_expired"]
    data: RiskReservationEventPayload


class RiskCircuitOpenedEvent(_RiskEventBase, Protocol):
    kind: Literal["circuit_opened"]
    data: RiskCircuitEventPayload


class RiskCircuitClosedEvent(_RiskEventBase, Protocol):
    kind: Literal["circuit_closed"]
    data: RiskCircuitEventPayload


RiskEventVariant: TypeAlias = (
    RiskDecisionMadeEvent
    | RiskReservationReservedEvent
    | RiskReservationConsumedEvent
    | RiskReservationReleasedEvent
    | RiskReservationExpiredEvent
    | RiskCircuitOpenedEvent
    | RiskCircuitClosedEvent
)


def decode_event(frame: bytes) -> RiskEventVariant:
    return cast(
        RiskEventVariant,
        import_module("kairospy._native_risk_contract").decode_event(frame),
    )


if not TYPE_CHECKING:
    RiskEvent = import_module("kairospy._native_risk_contract").RiskEvent


__all__ = [
    "RiskCircuitClosedEvent",
    "RiskCircuitOpenedEvent",
    "RiskDecisionMadeEvent",
    "RiskEvent",
    "RiskEventVariant",
    "RiskReservationConsumedEvent",
    "RiskReservationExpiredEvent",
    "RiskReservationReleasedEvent",
    "RiskReservationReservedEvent",
    "decode_event",
]
