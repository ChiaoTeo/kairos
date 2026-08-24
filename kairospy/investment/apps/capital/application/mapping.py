"""Explicit mappings between Capital application models and contract records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal
import time

from kairospy.primitives.account import AccountId, SegmentKey

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
    objective: FundingObjective, *, scope: Mapping[str, object]
) -> dict[str, object]:
    return {
        **scope,
        "objective_id": objective.objective_id,
        "version": objective.version,
        "destination": funding_location_record(objective.destination),
        "desired_available": format(objective.desired_available, "f"),
        "required_by_unix_nanos": _nanos(objective.required_by),
        "expires_at_unix_nanos": _nanos(objective.expires_at),
        "priority": objective.priority.value,
        "confidence_bps": int(objective.confidence * 10_000),
        "strategy_decision_id": objective.strategy_decision_id
        or str(scope["request_id"]),
        "observed_at_unix_nanos": (
            _nanos(objective.observed_at)
            if objective.observed_at is not None
            else time.time_ns()
        ),
    }


def capital_demand_request(
    demand: CapitalDemand, *, scope: Mapping[str, object]
) -> dict[str, object]:
    return {
        **scope,
        "demand_id": demand.demand_id,
        "idempotency_key": demand.idempotency_key,
        "destination": funding_location_record(demand.destination),
        "observed_shortfall": format(demand.observed_shortfall, "f"),
        "observed_at_unix_nanos": _nanos(demand.observed_at),
        "required_by_unix_nanos": _nanos(demand.required_by),
        "expires_at_unix_nanos": _nanos(demand.expires_at),
        "priority": demand.priority.value,
        "confidence_bps": int(demand.confidence * 10_000),
        "account_watermark": demand.account_watermark,
        "risk_watermark": demand.risk_watermark,
        "destination_lease_fence": demand.destination_lease_fence,
        "causal_references": list(demand.causal_references),
    }


def funding_location_record(value: FundingLocation) -> dict[str, str]:
    return {
        "broker": value.broker,
        "account_id": str(value.account_id),
        "segment": str(value.segment),
        "asset": value.asset,
    }


def map_capital_availability(value: object) -> CapitalAvailability:
    record = _mapping(value, "Capital availability")
    location = _mapping(record.get("location"), "Capital funding location")
    return CapitalAvailability(
        capital_group_id=_optional_text(record.get("capital_group_id")),
        readiness=CapitalReadiness(str(record["readiness"])),
        location=FundingLocation(
            broker=str(location["broker"]),
            account_id=AccountId(str(location["account_id"])),
            segment=SegmentKey(str(location["segment"])),
            asset=str(location["asset"]),
        ),
        policy_minimum=_decimal(record.get("policy_minimum")),
        policy_default_target=_decimal(record.get("policy_default_target")),
        policy_maximum=_decimal(record.get("policy_maximum")),
        policy_version=_optional_int(record.get("policy_version")),
        active_objective_ids=_strings(record.get("active_objective_ids", ())),
        active_demand_ids=_strings(record.get("active_demand_ids", ())),
        funding_horizons=tuple(
            _map_funding_horizon(item)
            for item in _sequence(record.get("funding_horizons", ()), "funding_horizons")
        ),
        desired_target=_decimal(record.get("desired_target")),
        observed_available=_decimal(record.get("observed_available")),
        effective_target=_decimal(record.get("effective_target")),
        deficit=_decimal(record.get("deficit")),
        account_watermark=_optional_int(record.get("account_watermark")),
        risk_policy_version=_optional_int(record.get("risk_policy_version")),
        risk_watermark=_optional_int(record.get("risk_watermark")),
        reason=_optional_text(record.get("reason")),
    )


def map_capital_alert(value: object) -> CapitalRecoveryAlert:
    record = _mapping(value, "Capital recovery alert")
    return CapitalRecoveryAlert(
        alert_id=str(record["alert_id"]),
        plan_id=str(record["plan_id"]),
        operation_id=_optional_text(record.get("operation_id")),
        kind=CapitalAlertKind(str(record["kind"])),
        severity=CapitalAlertSeverity(str(record["severity"])),
        recovery_action=CapitalRecoveryAction(str(record["recovery_action"])),
        message=str(record["message"]),
        opened_at=datetime.fromtimestamp(
            _required_int(record.get("opened_at_unix_nanos"), "opened_at_unix_nanos")
            / 1_000_000_000,
            tz=timezone.utc,
        ),
    )


def _map_funding_horizon(value: object) -> CapitalFundingHorizon:
    record = _mapping(value, "Capital funding horizon")
    return CapitalFundingHorizon(
        required_by=datetime.fromtimestamp(
            _required_int(
                record.get("required_by_unix_nanos"), "required_by_unix_nanos"
            )
            / 1_000_000_000,
            tz=timezone.utc,
        ),
        objective_ids=_strings(record.get("objective_ids", ())),
        demand_ids=_strings(record.get("demand_ids", ())),
        desired_available=_decimal(record.get("desired_available")) or Decimal("0"),
    )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    return value


def _strings(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in _sequence(value, "string collection"))


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _optional_int(value: object) -> int | None:
    return None if value is None else _required_int(value, "integer")


def _required_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError(f"{name} must be an integer")
    return int(value)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _nanos(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)
