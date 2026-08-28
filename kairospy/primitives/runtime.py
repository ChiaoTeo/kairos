from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

from ._text import TextValue


@dataclass(frozen=True, slots=True)
class WorkspaceId(TextValue):
    """Canonical workspace identity."""


@dataclass(frozen=True, slots=True)
class LaunchId(TextValue):
    """Canonical launch identity."""


@dataclass(frozen=True, slots=True)
class InstanceId(TextValue):
    """Canonical process instance identity."""


@dataclass(frozen=True, slots=True)
class StrategyId(TextValue):
    """Canonical Strategy identity."""


@dataclass(frozen=True, slots=True)
class RequestId(TextValue):
    """Canonical request identity."""


@dataclass(frozen=True, slots=True)
class ActorId(TextValue):
    """Canonical runtime actor identity."""


@dataclass(frozen=True, slots=True)
class ProducerId(TextValue):
    """Canonical event producer identity."""


@dataclass(frozen=True, slots=True)
class EventId(TextValue):
    """Canonical event identity."""


@dataclass(frozen=True, slots=True)
class StrategyDecisionId(TextValue):
    """Canonical Strategy decision identity."""


@dataclass(frozen=True, slots=True)
class IdempotencyKey(TextValue):
    """Canonical idempotency identity."""


WorkspaceIdRead = NewType("WorkspaceIdRead", str)
LaunchIdRead = NewType("LaunchIdRead", str)
InstanceIdRead = NewType("InstanceIdRead", str)
StrategyIdRead = NewType("StrategyIdRead", str)
RequestIdRead = NewType("RequestIdRead", str)
ActorIdRead = NewType("ActorIdRead", str)
ProducerIdRead = NewType("ProducerIdRead", str)
EventIdRead = NewType("EventIdRead", str)
StrategyDecisionIdRead = NewType("StrategyDecisionIdRead", str)
IdempotencyKeyRead = NewType("IdempotencyKeyRead", str)


__all__ = [
    "ActorId",
    "ActorIdRead",
    "EventId",
    "EventIdRead",
    "IdempotencyKey",
    "IdempotencyKeyRead",
    "InstanceId",
    "InstanceIdRead",
    "LaunchId",
    "LaunchIdRead",
    "ProducerId",
    "ProducerIdRead",
    "RequestId",
    "RequestIdRead",
    "StrategyId",
    "StrategyIdRead",
    "StrategyDecisionId",
    "StrategyDecisionIdRead",
    "WorkspaceId",
    "WorkspaceIdRead",
]
