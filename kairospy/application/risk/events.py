from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from kairospy.domain_types import DataEvent

from .models import ReservationChange, RiskCircuitChange, RiskDecisionChange


@dataclass(frozen=True, slots=True)
class RiskEventRecord:
    stream_id: str
    sequence: int
    producer: str
    kind: str
    account_id: str | None
    strategy_id: str | None
    payload: object
    occurred_at_unix_nanos: int
    launch_id: str | None = None
    instance_id: str | None = None

    def __post_init__(self) -> None:
        if not self.stream_id.strip() or self.sequence <= 0:
            raise ValueError("Risk event stream and positive sequence are required")


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
