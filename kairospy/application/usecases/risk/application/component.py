from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..domain import (
    RiskAllocation,
    RiskBudget,
    RiskDecision,
    RiskReservation,
    RiskReservationStatus,
    RiskUsage,
)
from ..services.ledger import RiskBudgetLedger


@dataclass(frozen=True, slots=True)
class RiskAssessmentRequest:
    request_id: str
    usages: tuple[RiskUsage, ...]
    at: datetime

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("risk request_id cannot be empty")
        if self.at.tzinfo is None:
            raise ValueError("risk request timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class RiskAssessmentResult:
    request_id: str
    decision: RiskDecision
    requested: tuple[RiskUsage, ...]
    allocations: tuple[RiskAllocation, ...]
    violations: tuple[str, ...]
    evaluated_at: datetime

@dataclass(frozen=True, slots=True)
class RiskReservationRequest:
    reservation_id: str
    assessment: RiskAssessmentRequest


@dataclass(frozen=True, slots=True)
class RiskReservationResult:
    reservation: RiskReservation
    decision: RiskDecision
    violations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RiskSnapshot:
    budgets: tuple[RiskBudget, ...]
    reservations: tuple[RiskReservation, ...]
    as_of: datetime | None


class RiskApplication:
    """Public risk budget usecase API."""

    def __init__(self, ledger: RiskBudgetLedger | None = None) -> None:
        self._ledger = ledger or RiskBudgetLedger()

    def configure(self, budgets: tuple[RiskBudget, ...]) -> None:
        self._ledger.replace_budgets(budgets)

    def assess(self, request: RiskAssessmentRequest) -> RiskAssessmentResult:
        decision, allocations, violations = self._ledger.assess(request.usages, at=request.at)
        return RiskAssessmentResult(
            request.request_id,
            decision,
            request.usages,
            allocations,
            violations,
            request.at,
        )

    def reserve(self, request: RiskReservationRequest) -> RiskReservationResult:
        assessment = self.assess(request.assessment)
        if assessment.decision is RiskDecision.REJECTED:
            raise ValueError("; ".join(assessment.violations))
        reservation = RiskReservation(
            request.reservation_id,
            request.assessment.request_id,
            assessment.allocations,
            RiskReservationStatus.RESERVED,
            request.assessment.at,
        )
        stored = self._ledger.reserve(reservation, usages=request.assessment.usages)
        return RiskReservationResult(stored, assessment.decision, assessment.violations)

    def release(self, reservation_id: str) -> RiskReservation:
        return self._ledger.release(reservation_id)

    def consume(self, reservation_id: str) -> RiskReservation:
        return self._ledger.consume(reservation_id)

    def snapshot(self, *, as_of: datetime | None = None) -> RiskSnapshot:
        return RiskSnapshot(self._ledger.budgets(), self._ledger.reservations(), as_of)


__all__ = [
    "RiskApplication",
    "RiskAssessmentRequest",
    "RiskAssessmentResult",
    "RiskReservationRequest",
    "RiskReservationResult",
    "RiskSnapshot",
]
