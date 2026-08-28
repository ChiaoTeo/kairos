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
class CapitalRouteId(TextValue):
    """Canonical Capital route identity."""


@dataclass(frozen=True, slots=True)
class CapitalPlanId(TextValue):
    """Canonical Capital plan identity."""


@dataclass(frozen=True, slots=True)
class CapitalReservationId(TextValue):
    """Canonical Capital reservation identity."""


@dataclass(frozen=True, slots=True)
class CapitalOperationId(TextValue):
    """Canonical Capital operation identity."""


@dataclass(frozen=True, slots=True)
class CapitalSourceAuthority(TextValue):
    """Canonical authority that governs a Capital source."""


@dataclass(frozen=True, slots=True)
class EarnProductId(TextValue):
    """Canonical earn-product identity."""


CapitalGroupIdRead = NewType("CapitalGroupIdRead", str)
FundingObjectiveIdRead = NewType("FundingObjectiveIdRead", str)
CapitalDemandIdRead = NewType("CapitalDemandIdRead", str)
CapitalRouteIdRead = NewType("CapitalRouteIdRead", str)
CapitalPlanIdRead = NewType("CapitalPlanIdRead", str)
CapitalReservationIdRead = NewType("CapitalReservationIdRead", str)
CapitalOperationIdRead = NewType("CapitalOperationIdRead", str)
CapitalSourceAuthorityRead = NewType("CapitalSourceAuthorityRead", str)
EarnProductIdRead = NewType("EarnProductIdRead", str)


__all__ = [
    "CapitalDemandId",
    "CapitalDemandIdRead",
    "CapitalGroupId",
    "CapitalGroupIdRead",
    "CapitalOperationId",
    "CapitalOperationIdRead",
    "CapitalPlanId",
    "CapitalPlanIdRead",
    "CapitalReservationId",
    "CapitalReservationIdRead",
    "CapitalRouteId",
    "CapitalRouteIdRead",
    "CapitalSourceAuthority",
    "CapitalSourceAuthorityRead",
    "EarnProductId",
    "EarnProductIdRead",
    "FundingObjectiveId",
    "FundingObjectiveIdRead",
]
