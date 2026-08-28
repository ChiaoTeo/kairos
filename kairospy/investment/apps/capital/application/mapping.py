"""Explicit mappings between Capital application models and contract records."""

from __future__ import annotations

from datetime import datetime
import time

from kairospy.contracts.capital.types import (
    CapitalAlert as ContractCapitalAlert,
    CapitalAvailability as ContractCapitalAvailability,
    FundingLocation as ContractFundingLocation,
    ObserveCapitalDemandRequest,
    PublishFundingObjectiveRequest,
)
from kairospy.primitives.account import AccountId, BrokerId, SegmentKey
from kairospy.primitives.capital import (
    CapitalDemandId,
    CapitalGroupId,
    CapitalOperationId,
    CapitalPlanId,
    FundingObjectiveId,
)
from kairospy.primitives.decimal import QuantityLike
from kairospy.primitives.runtime import InstanceId, LaunchId, RequestId, StrategyId
from kairospy.primitives.reference import AssetId
from kairospy.primitives.time import Sequence, datetime_from_unix_nanos

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
    request_id: RequestId,
    capital_group_id: CapitalGroupId,
    strategy_id: StrategyId,
) -> PublishFundingObjectiveRequest:
    return PublishFundingObjectiveRequest(
        str(request_id),
        str(capital_group_id),
        str(objective.objective_id),
        objective.version,
        str(strategy_id),
        contract_funding_location(objective.destination),
        objective.desired_available,
        _nanos(objective.required_by),
        _nanos(objective.expires_at),
        objective.priority.value,
        int(objective.confidence.value * 10_000),
        str(objective.strategy_decision_id or request_id),
        (
            _nanos(objective.observed_at)
            if objective.observed_at is not None
            else time.time_ns()
        ),
    )


def capital_demand_request(
    demand: CapitalDemand,
    *,
    request_id: RequestId,
    capital_group_id: CapitalGroupId,
    strategy_id: StrategyId,
    launch_id: LaunchId,
    instance_id: InstanceId,
) -> ObserveCapitalDemandRequest:
    return ObserveCapitalDemandRequest(
        str(request_id),
        str(demand.demand_id),
        str(demand.idempotency_key),
        str(capital_group_id),
        str(strategy_id),
        contract_funding_location(demand.destination),
        demand.observed_shortfall,
        _nanos(demand.observed_at),
        _nanos(demand.required_by),
        _nanos(demand.expires_at),
        demand.priority.value,
        int(demand.confidence.value * 10_000),
        demand.account_watermark,
        demand.risk_watermark,
        str(launch_id),
        str(instance_id),
        demand.destination_lease_fence,
        list(demand.causal_references),
    )


def contract_funding_location(value: FundingLocation) -> ContractFundingLocation:
    return ContractFundingLocation(
        str(value.broker),
        str(value.account_id),
        str(value.segment),
        str(value.asset),
    )


def map_capital_availability(
    value: ContractCapitalAvailability,
    *,
    capital_group_id: CapitalGroupId | None,
) -> CapitalAvailability:
    location = value.location
    return CapitalAvailability(
        capital_group_id=capital_group_id,
        readiness=CapitalReadiness(str(value.readiness)),
        location=FundingLocation(
            broker=BrokerId(location.broker),
            account_id=AccountId(str(location.account_id)),
            segment=SegmentKey(str(location.segment)),
            asset=AssetId(location.asset),
        ),
        policy_version=value.policy_version,
        active_objective_ids=tuple(
            FundingObjectiveId(value) for value in value.active_objective_ids
        ),
        active_demand_ids=tuple(
            CapitalDemandId(value) for value in value.active_demand_ids
        ),
        funding_horizons=tuple(
            CapitalFundingHorizon(
                required_by=datetime_from_unix_nanos(item.required_by_unix_nanos),
                objective_ids=tuple(
                    FundingObjectiveId(identity) for identity in item.objective_ids
                ),
                demand_ids=tuple(
                    CapitalDemandId(identity) for identity in item.demand_ids
                ),
                desired_available=item.desired_available,
            )
            for item in value.funding_horizons
        ),
        desired_target=value.desired_target,
        observed_available=value.observed_available,
        effective_target=value.effective_target,
        deficit=value.deficit,
        account_watermark=Sequence(value.account_watermark),
        risk_policy_version=value.risk_policy_version,
        risk_watermark=Sequence(value.risk_watermark),
        reason=value.reason,
    )


def map_capital_alert(value: ContractCapitalAlert) -> CapitalRecoveryAlert:
    return CapitalRecoveryAlert(
        alert_id=value.alert_id,
        plan_id=CapitalPlanId(value.plan_id),
        operation_id=(
            None
            if value.operation_id is None
            else CapitalOperationId(value.operation_id)
        ),
        kind=CapitalAlertKind(value.kind),
        severity=CapitalAlertSeverity(value.severity),
        recovery_action=CapitalRecoveryAction(value.recovery_action),
        message=value.message,
        opened_at=datetime_from_unix_nanos(value.opened_at_unix_nanos),
    )


def _nanos(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)
