"""Commands handled by the Risk Actor."""

from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.usecases.risk.application.budget import (
    RiskAssessmentRequest,
    RiskReservationRequest,
)
from kairospy.application.usecases.risk.application.budget import RiskBudget


@dataclass(frozen=True, slots=True)
class ConfigureRiskBudgetsCommand:
    budgets: tuple[RiskBudget, ...]


@dataclass(frozen=True, slots=True)
class AssessRiskCommand:
    request: RiskAssessmentRequest


@dataclass(frozen=True, slots=True)
class ReserveRiskCommand:
    request: RiskReservationRequest


@dataclass(frozen=True, slots=True)
class ReleaseRiskCommand:
    reservation_id: str


@dataclass(frozen=True, slots=True)
class ConsumeRiskCommand:
    reservation_id: str


__all__ = [
    "AssessRiskCommand",
    "ConfigureRiskBudgetsCommand",
    "ConsumeRiskCommand",
    "ReleaseRiskCommand",
    "ReserveRiskCommand",
]
