from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from kairospy.investment.apps.capital.application import (
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
from kairospy.primitives.account import AccountId, SegmentKey
from kairospy.primitives.decimal import Quantity
from kairospy.primitives.runtime import InstanceId, LaunchId
from kairospy.contracts.capital import CapitalCurrentView
from kairospy.investment.apps.capital.application.mapping import map_capital_alert


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


def test_capital_live_events_follow_the_single_synchronous_poll_path() -> None:
    event = SimpleNamespace(
        metadata=SimpleNamespace(
            stream_id="capital.events",
            producer="capital",
            producer_incarnation=1,
            sequence=7,
            launch_id=LaunchId("launch-a"),
            instance_id=InstanceId("instance-a"),
        )
    )

    class LiveSource:
        closed = False

        def poll_visit(self, visitor, *, fragment_limit=64):
            visitor(event)
            return 1

        def close(self):
            self.closed = True

    source = LiveSource()
    capital = CapitalApplication(
        None,
        None,
        source,
        strategy_id="basis",
        launch_id="launch-a",
        instance_id="instance-a",
        capital_group_id=None,
    )
    observed: list[object] = []

    assert capital.visit_live(observed.append) == 1
    assert observed == [event]
    assert capital.notification_health()["cursor"] == 7
    capital.close_live()
    assert source.closed


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
        def publish_funding_objective(self, request):
            assert request.objective_id == str(_objective().objective_id)
            assert request.capital_group_id == "group-a"
            assert request.strategy_id == "basis"
            assert not hasattr(request, "source")
            return FundingObjectiveReceipt(
                str(request.objective_id),
                int(request.version),
                FundingObjectiveStatus.ACCEPTED,
            )

    class CurrentView:
        def snapshot(self):
            class Snapshot:
                alerts = ()

                def availability(self, location):
                    assert location is not None
                    broker, account_id, segment, asset = location
                    return SimpleNamespace(
                        readiness="ready",
                        location=SimpleNamespace(
                            broker=broker,
                            account_id=account_id,
                            segment=segment,
                            asset=asset,
                        ),
                        policy_version=2,
                        active_objective_ids=("buffer-usdt",),
                        active_demand_ids=(),
                        funding_horizons=(),
                        desired_target=Quantity("80000"),
                        observed_available=Quantity("50000"),
                        effective_target=Quantity("80000"),
                        deficit=Quantity("30000"),
                        account_watermark=41,
                        risk_policy_version=3,
                        risk_watermark=4,
                        reason=None,
                    )

            return Snapshot()

    capital = CapitalApplication(
        Commands(),
        CurrentView(),
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
    assert capital.availability(_objective().destination).deficit.value == Decimal("30000")


def test_typed_historical_forecast_becomes_a_deterministic_funding_objective() -> None:
    captured: list[object] = []

    class Commands:
        def publish_funding_objective(self, request):
            captured.append(request)
            return FundingObjectiveReceipt(
                str(request.objective_id),
                int(request.version),
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
    assert getattr(captured[0], "desired_available").value == Decimal("90")
    assert getattr(captured[0], "observed_at_unix_nanos") == int(observed_at.timestamp() * 1_000_000_000)
    assert getattr(captured[0], "strategy_decision_id") == (
        "forecast:historical_peak:session-usdt-peak:3"
    )
    assert not hasattr(captured[0], "source_account")


def test_availability_transport_failure_is_not_hidden_by_a_python_fallback() -> None:
    class CurrentView:
        def snapshot(self):
            class Snapshot:
                def availability(self, _location):
                    raise RuntimeError("snapshot is warming up")

            return Snapshot()

    capital = CapitalApplication(
        object(),
        CurrentView(),
        strategy_id="basis",
        launch_id="launch-a",
        instance_id="instance-a",
        capital_group_id="group-a",
        account_ids=(AccountId("account-a"),),
    )

    with pytest.raises(RuntimeError, match="warming up"):
        capital.availability(_objective().destination)


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
    observed: list[object] = []

    class Commands:
        def observe_capital_demand(self, request):
            observed.append(request)
            return SimpleNamespace(
                demand_id=request.demand_id,
                status="accepted",
                error_message=None,
            )

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
    assert getattr(observed[0], "demand_id") == str(demand.demand_id)
    assert getattr(observed[0], "capital_group_id") == "group-a"
    assert not hasattr(observed[0], "source")

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
    path = CapitalCurrentView(tmp_path, "group/../一", "workspace").path

    assert path.parent.name == "epoch-1"
    assert path.name == "current.lmdb"
    assert path.is_relative_to(tmp_path)
    assert "/../" not in str(path)


def test_capital_recovery_alert_decoder_preserves_operator_evidence() -> None:
    alert = map_capital_alert(
        SimpleNamespace(
            alert_id="capital-recovery:plan-a",
            plan_id="plan-a",
            operation_id="operation-a",
            kind="manual_review",
            severity="critical",
            recovery_action="hold_and_review",
            message="hold funds and review",
            opened_at_unix_nanos=1_787_200_000_000_000_000,
        )
    )

    assert str(alert.plan_id) == "plan-a"
    assert alert.operation_id is not None
    assert str(alert.operation_id) == "operation-a"
    assert alert.kind is CapitalAlertKind.MANUAL_REVIEW
    assert alert.severity is CapitalAlertSeverity.CRITICAL
    assert alert.recovery_action is CapitalRecoveryAction.HOLD_AND_REVIEW
    assert alert.opened_at.tzinfo is timezone.utc
