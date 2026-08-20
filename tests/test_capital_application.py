from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kairospy.application.capital import (
    CapitalApplication,
    CapitalAlertKind,
    CapitalAlertSeverity,
    CapitalAvailability,
    CapitalDemand,
    CapitalReadiness,
    CapitalRecoveryAction,
    FundingForecastObservation,
    FundingForecastSource,
    FundingLocation,
    FundingObjective,
    FundingObjectiveReceipt,
    FundingObjectiveStatus,
)
from kairospy.domain_types import AccountId, SegmentKey
from kairospy.infrastructure.contracts.capital.view import (
    CapitalViewKey,
    _recovery_alert,
    decode_view,
)


def _objective(account: str = "account-a") -> FundingObjective:
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    return FundingObjective(
        objective_id="buffer-usdt",
        version=2,
        destination=FundingLocation(AccountId(account), SegmentKey("usd-m"), "usdt"),
        desired_available=Decimal("80000"),
        required_by=now + timedelta(minutes=5),
        expires_at=now + timedelta(hours=1),
        confidence=Decimal("0.8"),
        strategy_decision_id="decision-7",
    )


def test_disabled_capital_is_a_typed_non_blocking_strategy_outcome() -> None:
    capital = CapitalApplication.disabled(
        strategy_id="basis",
        launch_id="launch-a",
        instance_id="instance-a",
        account_ids=(AccountId("account-a"),),
    )

    receipt = capital.publish_objective(_objective())
    availability = capital.availability(_objective().destination)

    assert receipt.status is FundingObjectiveStatus.DISABLED
    assert availability.readiness is CapitalReadiness.DISABLED
    assert capital.enabled is False


def test_strategy_cannot_publish_an_objective_outside_its_group() -> None:
    capital = CapitalApplication(
        object(),
        None,
        strategy_id="basis",
        launch_id="launch-a",
        instance_id="instance-a",
        capital_group_id="group-a",
        account_ids=(AccountId("account-a"),),
    )

    receipt = capital.publish_objective(_objective("account-b"))

    assert receipt.status is FundingObjectiveStatus.REJECTED
    assert "outside" in (receipt.message or "")


def test_enabled_facade_adds_identity_but_does_not_select_a_route() -> None:
    class Commands:
        def publish_funding_objective(self, objective, **identity):
            assert objective == _objective()
            assert identity["capital_group_id"] == "group-a"
            assert identity["strategy_id"] == "basis"
            assert not hasattr(objective, "source")
            return FundingObjectiveReceipt(
                objective.objective_id,
                objective.version,
                FundingObjectiveStatus.ACCEPTED,
            )

    class Projection:
        def availability(self, **query):
            return CapitalAvailability(
                query["capital_group_id"],
                CapitalReadiness.READY,
                location=query["location"],
                observed_available=Decimal("50000"),
                effective_target=Decimal("80000"),
                deficit=Decimal("30000"),
                account_watermark=41,
            )

    capital = CapitalApplication(
        Commands(),
        Projection(),
        strategy_id="basis",
        launch_id="launch-a",
        instance_id="instance-a",
        capital_group_id="group-a",
        account_ids=(AccountId("account-a"),),
    )

    assert (
        capital.publish_objective(_objective()).status
        is FundingObjectiveStatus.ACCEPTED
    )
    assert capital.availability(_objective().destination).deficit == Decimal("30000")


def test_typed_historical_forecast_becomes_a_deterministic_funding_objective() -> None:
    captured: list[FundingObjective] = []

    class Commands:
        def publish_funding_objective(self, objective, **_identity):
            captured.append(objective)
            return FundingObjectiveReceipt(
                objective.objective_id,
                objective.version,
                FundingObjectiveStatus.ACCEPTED,
            )

    capital = CapitalApplication(
        Commands(),
        None,
        strategy_id="basis",
        launch_id="launch-a",
        instance_id="instance-a",
        capital_group_id="group-a",
        account_ids=(AccountId("account-a"),),
    )
    observed_at = datetime(2026, 8, 20, tzinfo=timezone.utc)
    forecast = FundingForecastObservation.from_historical_peak(
        forecast_id="session-usdt-peak",
        version=3,
        destination=FundingLocation(
            AccountId("account-a"), SegmentKey("usd-m"), "USDT"
        ),
        observed_samples=(Decimal("50"), Decimal("80"), Decimal("65")),
        safety_buffer=Decimal("10"),
        observed_at=observed_at,
        required_by=observed_at + timedelta(hours=1),
        expires_at=observed_at + timedelta(hours=2),
        evidence_references=("history:30d:usd-m",),
    )

    receipt = capital.publish_forecast(forecast)

    assert receipt.status is FundingObjectiveStatus.ACCEPTED
    assert forecast.source is FundingForecastSource.HISTORICAL_PEAK
    assert captured[0].desired_available == Decimal("90")
    assert captured[0].observed_at == observed_at
    assert captured[0].strategy_decision_id == (
        "forecast:historical_peak:session-usdt-peak:3"
    )
    assert not hasattr(captured[0], "source_account")


def test_availability_transport_failure_degrades_without_blocking_strategy() -> None:
    class Projection:
        def availability(self, **_query):
            raise RuntimeError("snapshot is warming up")

    capital = CapitalApplication(
        object(),
        Projection(),
        strategy_id="basis",
        launch_id="launch-a",
        instance_id="instance-a",
        capital_group_id="group-a",
        account_ids=(AccountId("account-a"),),
    )

    availability = capital.availability(_objective().destination)

    assert availability.readiness is CapitalReadiness.DEGRADED
    assert "warming up" in (availability.reason or "")


def test_funding_objective_requires_a_bounded_time_window() -> None:
    objective = _objective()

    with pytest.raises(ValueError, match="expire before"):
        FundingObjective(
            objective.objective_id,
            objective.version,
            objective.destination,
            objective.desired_available,
            objective.expires_at,
            objective.required_by,
        )


def test_demand_is_advisory_scoped_and_carries_fencing_evidence() -> None:
    observed: list[tuple[CapitalDemand, dict[str, object]]] = []

    class Commands:
        def observe_capital_demand(self, demand, **identity):
            observed.append((demand, identity))
            return {"demand_id": demand.demand_id, "status": "accepted"}

    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    demand = CapitalDemand(
        demand_id="risk:decision-7:order-9",
        idempotency_key="risk:decision-7:order-9",
        destination=FundingLocation("account-a", "usd-m", "USDT"),
        observed_shortfall=Decimal("30000"),
        observed_at=now,
        required_by=now,
        expires_at=now + timedelta(seconds=60),
        account_watermark=41,
        risk_watermark=57,
        destination_lease_fence="lease-fence-7",
        causal_references=("execution-order:order-9",),
    )
    capital = CapitalApplication(
        Commands(),
        None,
        strategy_id="basis",
        launch_id="launch-a",
        instance_id="instance-a",
        capital_group_id="group-a",
        account_ids=(AccountId("account-a"),),
        account_lease_fences={AccountId("account-a"): "lease-fence-7"},
    )

    receipt = capital.observe_demand(demand)

    assert receipt.status is FundingObjectiveStatus.ACCEPTED
    assert observed[0][0] == demand
    assert observed[0][1]["capital_group_id"] == "group-a"
    assert not hasattr(demand, "source")

    stale = CapitalApplication(
        Commands(),
        None,
        strategy_id="basis",
        launch_id="launch-a",
        instance_id="instance-a",
        capital_group_id="group-a",
        account_ids=(AccountId("account-a"),),
        account_lease_fences={AccountId("account-a"): "new-fence"},
    ).observe_demand(demand)
    assert stale.status is FundingObjectiveStatus.REJECTED
    assert len(observed) == 1


def test_capital_view_key_matches_the_rust_resource_topology(tmp_path) -> None:
    key = CapitalViewKey("group/../一")

    path = key.resource_path(tmp_path)

    assert path.parent.name == "current"
    assert path.name == "current.snapshot"
    assert path.is_relative_to(tmp_path)
    assert "/../" not in str(path)
    assert key.canonical_key().startswith("capital.current/")


def test_capital_view_decoder_fails_closed_on_another_root() -> None:
    with pytest.raises(ValueError, match="CPV2"):
        decode_view(b"\0\0\0\0NOPE")


def test_capital_recovery_alert_decoder_preserves_operator_evidence() -> None:
    class Row:
        def AlertId(self):
            return b"capital-recovery:plan-a"

        def PlanId(self):
            return b"plan-a"

        def OperationId(self):
            return b"operation-a"

        def Kind(self):
            return 1

        def Severity(self):
            return 1

        def RecoveryAction(self):
            return 3

        def Message(self):
            return b"hold funds and review"

        def OpenedAtUnixNanos(self):
            return 1_787_200_000_000_000_000

    alert = _recovery_alert(Row())

    assert alert.plan_id == "plan-a"
    assert alert.operation_id == "operation-a"
    assert alert.kind is CapitalAlertKind.MANUAL_REVIEW
    assert alert.severity is CapitalAlertSeverity.CRITICAL
    assert alert.recovery_action is CapitalRecoveryAction.HOLD_AND_REVIEW
    assert alert.opened_at.tzinfo is timezone.utc
