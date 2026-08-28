from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

from ._text import TextValue


@dataclass(frozen=True, slots=True)
class CapitalGroupId(TextValue):
    """Canonical Capital group identity."""


@dataclass(frozen=True, slots=True)
class FundingObjectiveId(TextValue):
    """Canonical funding objective identity."""


@dataclass(frozen=True, slots=True)
class CapitalDemandId(TextValue):
    """Canonical Capital demand identity."""


@dataclass(frozen=True, slots=True)
class CapitalPlanId(TextValue):
    """Canonical Capital plan identity."""


@dataclass(frozen=True, slots=True)
class CapitalReservationId(TextValue):
    """Canonical Capital reservation identity."""


CapitalGroupIdRead = NewType("CapitalGroupIdRead", str)
FundingObjectiveIdRead = NewType("FundingObjectiveIdRead", str)
CapitalDemandIdRead = NewType("CapitalDemandIdRead", str)
CapitalPlanIdRead = NewType("CapitalPlanIdRead", str)
CapitalReservationIdRead = NewType("CapitalReservationIdRead", str)


__all__ = [
    "CapitalDemandId",
    "CapitalDemandIdRead",
    "CapitalGroupId",
    "CapitalGroupIdRead",
    "CapitalPlanId",
    "CapitalPlanIdRead",
    "CapitalReservationId",
    "CapitalReservationIdRead",
    "FundingObjectiveId",
    "FundingObjectiveIdRead",
]
