"""Capital-owned classified event contract."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, cast

from kairospy.infrastructure.protocol.eventing import EventMetadataRead

if TYPE_CHECKING:
    from kairospy._native_capital_contract import (
        CapitalAvailabilityEventPayload,
        CapitalDemand,
        CapitalEvent,
        CapitalFacts,
        CapitalPlanEventPayload,
        CapitalPolicy,
        CapitalRoute,
        FundingObjective,
    )


class _CapitalEventBase(Protocol):
    metadata: EventMetadataRead


class CapitalFundingObjectiveChangedEvent(_CapitalEventBase, Protocol):
    kind: Literal["funding_objective_changed"]
    data: FundingObjective


class CapitalDemandChangedEvent(_CapitalEventBase, Protocol):
    kind: Literal["capital_demand_changed"]
    data: CapitalDemand


class CapitalPolicyChangedEvent(_CapitalEventBase, Protocol):
    kind: Literal["policy_changed"]
    data: CapitalPolicy


class CapitalFactsObservedEvent(_CapitalEventBase, Protocol):
    kind: Literal["facts_observed"]
    data: CapitalFacts


class CapitalAvailabilityEvaluatedEvent(_CapitalEventBase, Protocol):
    kind: Literal["availability_evaluated"]
    data: CapitalAvailabilityEventPayload


class CapitalRouteChangedEvent(_CapitalEventBase, Protocol):
    kind: Literal["route_changed"]
    data: CapitalRoute


class CapitalPlanAuthorizedEvent(_CapitalEventBase, Protocol):
    kind: Literal["plan_authorized"]
    data: CapitalPlanEventPayload


class CapitalPlanStateChangedEvent(_CapitalEventBase, Protocol):
    kind: Literal["plan_state_changed"]
    data: CapitalPlanEventPayload


class CapitalPlanExpiredEvent(_CapitalEventBase, Protocol):
    kind: Literal["plan_expired"]
    data: CapitalPlanEventPayload


CapitalEventVariant: TypeAlias = (
    CapitalFundingObjectiveChangedEvent
    | CapitalDemandChangedEvent
    | CapitalPolicyChangedEvent
    | CapitalFactsObservedEvent
    | CapitalAvailabilityEvaluatedEvent
    | CapitalRouteChangedEvent
    | CapitalPlanAuthorizedEvent
    | CapitalPlanStateChangedEvent
    | CapitalPlanExpiredEvent
)


def decode_event(frame: bytes) -> CapitalEventVariant:
    return cast(
        CapitalEventVariant,
        import_module("kairospy._native_capital_contract").decode_event(frame),
    )


if not TYPE_CHECKING:
    CapitalEvent = import_module("kairospy._native_capital_contract").CapitalEvent


__all__ = [
    "CapitalAvailabilityEvaluatedEvent",
    "CapitalDemandChangedEvent",
    "CapitalEvent",
    "CapitalEventVariant",
    "CapitalFactsObservedEvent",
    "CapitalFundingObjectiveChangedEvent",
    "CapitalPlanAuthorizedEvent",
    "CapitalPlanExpiredEvent",
    "CapitalPlanStateChangedEvent",
    "CapitalPolicyChangedEvent",
    "CapitalRouteChangedEvent",
    "decode_event",
]
