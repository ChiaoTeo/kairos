from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from kairospy.investment.application.eventing import DataEvent
from kairospy.infrastructure.contracts.risk.records import RiskEventRecord

from .models import ReservationChange, RiskCircuitChange, RiskDecisionChange


@dataclass(frozen=True, slots=True)
class ReservationChangedEvent(DataEvent[ReservationChange]):
    kind: Literal["reservation_changed"] = field(
        init=False, default="reservation_changed"
    )


@dataclass(frozen=True, slots=True)
class RiskDecisionEvent(DataEvent[RiskDecisionChange]):
    kind: Literal["decision_evaluated"] = field(
        init=False, default="decision_evaluated"
    )


@dataclass(frozen=True, slots=True)
class RiskCircuitChangedEvent(DataEvent[RiskCircuitChange]):
    kind: Literal["circuit_changed"] = field(init=False, default="circuit_changed")


RiskEvent: TypeAlias = (
    ReservationChangedEvent | RiskDecisionEvent | RiskCircuitChangedEvent
)
