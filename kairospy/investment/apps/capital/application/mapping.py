"""Explicit mappings between Capital application models and contract records."""

from __future__ import annotations

from datetime import datetime, timezone
import time

from kairospy.infrastructure.contracts.capital.types import (
    FundingLocation as ContractFundingLocation,
    ObserveCapitalDemandRequest,
    PublishFundingObjectiveRequest,
)
from kairospy.primitives.account import AccountId, SegmentKey
from kairospy.primitives.decimal import Quantity, QuantityLike

from .models import (
    CapitalAlertKind,
    CapitalAlertSeverity,
    CapitalAvailability,
    CapitalDemand,
    CapitalFundingHorizon,
    CapitalReadiness,
    CapitalRecoveryAction,
    CapitalRecoveryAlert,
    FundingLocation,
    FundingObjective,
)


def funding_objective_request(
    objective: FundingObjective,
    *,
    request_id: str,
    capital_group_id: str,
    strategy_id: str,
) -> object:
    return PublishFundingObjectiveRequest(
        request_id,
        capital_group_id,
        objective.objective_id,
        objective.version,
        strategy_id,
        contract_funding_location(objective.destination),
        objective.desired_available,
        _nanos(objective.required_by),
        _nanos(objective.expires_at),
        objective.priority.value,
        int(objective.confidence.value * 10_000),
        objective.strategy_decision_id or request_id,
        (
            _nanos(objective.observed_at)
            if objective.observed_at is not None
            else time.time_ns()
        ),
    )


def capital_demand_request(
    demand: CapitalDemand,
    *,
    request_id: str,
    capital_group_id: str,
    strategy_id: str,
    launch_id: str,
    instance_id: str,
) -> object:
    return ObserveCapitalDemandRequest(
        request_id,
        demand.demand_id,
        demand.idempotency_key,
        capital_group_id,
        strategy_id,
        contract_funding_location(demand.destination),
        demand.observed_shortfall,
        _nanos(demand.observed_at),
        _nanos(demand.required_by),
        _nanos(demand.expires_at),
        demand.priority.value,
        int(demand.confidence.value * 10_000),
        demand.account_watermark,
        demand.risk_watermark,
        launch_id,
        instance_id,
        demand.destination_lease_fence,
        list(demand.causal_references),
    )


def contract_funding_location(value: FundingLocation) -> object:
    return ContractFundingLocation(
        value.broker,
        str(value.account_id),
        str(value.segment),
        value.asset,
    )


def map_capital_availability(value: object) -> CapitalAvailability:
    location = getattr(value, "location")
    return CapitalAvailability(
        capital_group_id=None,
        readiness=CapitalReadiness(str(getattr(value, "readiness"))),
        location=FundingLocation(
            broker=str(getattr(location, "broker")),
            account_id=AccountId(str(getattr(location, "account_id"))),
            segment=SegmentKey(str(getattr(location, "segment"))),
            asset=str(getattr(location, "asset")),
        ),
        policy_version=int(getattr(value, "policy_version")),
        active_objective_ids=tuple(getattr(value, "active_objective_ids")),
        active_demand_ids=tuple(getattr(value, "active_demand_ids")),
        funding_horizons=tuple(
            CapitalFundingHorizon(
                required_by=datetime.fromtimestamp(
                    int(getattr(item, "required_by_unix_nanos")) / 1_000_000_000,
                    tz=timezone.utc,
                ),
                objective_ids=tuple(getattr(item, "objective_ids")),
                demand_ids=tuple(getattr(item, "demand_ids")),
                desired_available=_quantity(getattr(item, "desired_available")),
            )
            for item in getattr(value, "funding_horizons")
        ),
        desired_target=_quantity(getattr(value, "desired_target")),
        observed_available=_quantity(getattr(value, "observed_available")),
        effective_target=_quantity(getattr(value, "effective_target")),
        deficit=_quantity(getattr(value, "deficit")),
        account_watermark=int(getattr(value, "account_watermark")),
        risk_policy_version=int(getattr(value, "risk_policy_version")),
        risk_watermark=int(getattr(value, "risk_watermark")),
        reason=getattr(value, "reason"),
    )


def map_capital_alert(value: object) -> CapitalRecoveryAlert:
    return CapitalRecoveryAlert(
        alert_id=str(getattr(value, "alert_id")),
        plan_id=str(getattr(value, "plan_id")),
        operation_id=getattr(value, "operation_id"),
        kind=CapitalAlertKind(str(getattr(value, "kind"))),
        severity=CapitalAlertSeverity(str(getattr(value, "severity"))),
        recovery_action=CapitalRecoveryAction(str(getattr(value, "recovery_action"))),
        message=str(getattr(value, "message")),
        opened_at=datetime.fromtimestamp(
            int(getattr(value, "opened_at_unix_nanos")) / 1_000_000_000,
            tz=timezone.utc,
        ),
    )


def _nanos(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def _quantity(value: object) -> QuantityLike:
    if isinstance(value, QuantityLike) and value.semantic_type == "quantity":
        return value
    if isinstance(value, (str, int)):
        return Quantity(value)
    raise TypeError("Capital contract value must be QuantityLike")
