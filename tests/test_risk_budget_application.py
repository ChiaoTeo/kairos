from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from kairospy.application.usecases.risk.application.budget import (
    RiskApplication,
    RiskAssessmentRequest,
    RiskReservationRequest,
)
from kairospy.application.usecases.risk.application.projector import RiskProjector
from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.actor.account.application.assembly import (
    AccountActorDependencies as BusinessRuntimeDependencies,
    compose_account_capabilities as compose_business_capabilities,
)
from kairospy.application.usecases.execution.application.runtime import build_execution_coordinator
from kairospy.domain.intent import IntentJournal
from kairospy.application.usecases.risk.domain import (
    BudgetRef,
    RiskBudget,
    RiskDecision,
    RiskMetric,
    RiskReservationStatus,
    RiskUsage,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
ACCOUNT = BudgetRef("account", "broker:account")


def usage(amount: str) -> RiskUsage:
    return RiskUsage(RiskMetric.NOTIONAL, Decimal(amount), (ACCOUNT,))


def app(limit: str = "100") -> RiskApplication:
    result = RiskApplication()
    result.configure((RiskBudget("account-notional", ACCOUNT, RiskMetric.NOTIONAL, Decimal(limit)),))
    return result


def test_assess_reports_rejection_without_mutating_budget() -> None:
    risk = app("100")

    result = risk.assess(RiskAssessmentRequest("request-1", (usage("101"),), NOW))

    assert result.decision is RiskDecision.REJECTED
    assert result.violations
    assert risk.snapshot().budgets[0].available == Decimal("100")


def test_reservation_is_idempotent_and_consumption_moves_to_used() -> None:
    risk = app("100")
    request = RiskAssessmentRequest("request-1", (usage("40"),), NOW)
    reservation = RiskReservationRequest("reservation-1", request)

    first = risk.reserve(reservation)
    second = risk.reserve(reservation)

    assert first.reservation == second.reservation
    assert risk.snapshot().budgets[0].reserved == Decimal("40")
    risk.consume("reservation-1")
    snapshot = risk.snapshot()
    assert snapshot.budgets[0].reserved == Decimal("0")
    assert snapshot.budgets[0].used == Decimal("40")
    assert snapshot.reservations[0].status is RiskReservationStatus.CONSUMED


def test_release_returns_reserved_capacity() -> None:
    risk = app("100")
    risk.reserve(
        RiskReservationRequest(
            "reservation-1",
            RiskAssessmentRequest("request-1", (usage("40"),), NOW),
        )
    )

    risk.release("reservation-1")

    snapshot = risk.snapshot()
    assert snapshot.budgets[0].available == Decimal("100")
    assert snapshot.reservations[0].status is RiskReservationStatus.RELEASED


def test_reservation_conflict_is_rejected() -> None:
    risk = app("100")
    risk.reserve(
        RiskReservationRequest(
            "reservation-1",
            RiskAssessmentRequest("request-1", (usage("40"),), NOW),
        )
    )

    with pytest.raises(ValueError, match="conflicting risk reservation"):
        risk.reserve(
            RiskReservationRequest(
                "reservation-1",
                RiskAssessmentRequest("request-2", (usage("20"),), NOW),
            )
        )


def test_one_assessment_cannot_overcommit_a_budget_across_usages() -> None:
    risk = app("100")

    result = risk.assess(
        RiskAssessmentRequest("request-1", (usage("60"), usage("60")), NOW)
    )

    assert result.decision is RiskDecision.REJECTED
    assert risk.snapshot().budgets[0].available == Decimal("100")


def test_risk_projector_publishes_budget_snapshot_for_readers() -> None:
    risk = app("100")
    projector = RiskProjector(risk)
    views = ViewStore()

    projector.register_views(views)
    projector.publish_views(views, as_of=NOW)

    payload = views.require("risk.budget")
    assert payload.budgets[0].budget_id == "account-notional"
    assert payload.as_of == NOW


def test_account_actor_composition_keeps_risk_on_execution_coordinator() -> None:
    coordinator = build_execution_coordinator()

    capabilities = compose_business_capabilities(
        BusinessRuntimeDependencies(
            intents=IntentJournal(),
            execution_coordinator=coordinator,
            risk=coordinator.risk,
        )
    )

    assert capabilities.execution_application is not None
    assert coordinator.risk is not None
