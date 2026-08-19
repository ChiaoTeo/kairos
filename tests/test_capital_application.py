from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kairospy.application.capital import (
    CapitalApplication,
    CapitalAvailability,
    CapitalDemand,
    CapitalReadiness,
    FundingLocation,
    FundingObjective,
    FundingObjectiveReceipt,
    FundingObjectiveStatus,
)
from kairospy.domain_types import AccountId, SegmentKey


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
