from __future__ import annotations

from pathlib import Path
import time
from decimal import Decimal

from kairospy.application.capital.models import (
    CapitalAvailability,
    CapitalDemand,
    CapitalReadiness,
    FundingLocation,
    FundingObjective,
)
from kairospy.infrastructure.transport.commands import UnixJsonRpcClient


class CapitalContractClient:
    """Low-frequency Strategy/Execution commands for one Capital process."""

    def __init__(self, socket_path: str | Path, *, timeout: float = 5.0) -> None:
        self._client = UnixJsonRpcClient(socket_path, timeout=timeout)

    def publish_funding_objective(
        self,
        objective: FundingObjective,
        **scope: object,
    ) -> dict[str, object]:
        return self._call(
            "capital_publish_funding_objective",
            {
                **scope,
                "objective_id": objective.objective_id,
                "version": objective.version,
                "destination": _location(objective.destination),
                "desired_available": str(objective.desired_available),
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
            },
        )

    def cancel_funding_objective(
        self, objective_id: str, *, expected_version: int, **scope: object
    ) -> dict[str, object]:
        return self._call(
            "capital_cancel_funding_objective",
            {
                **scope,
                "objective_id": objective_id,
                "expected_version": expected_version,
                "observed_at_unix_nanos": time.time_ns(),
            },
        )

    def observe_capital_demand(
        self, demand: CapitalDemand, **scope: object
    ) -> dict[str, object]:
        return self._call(
            "capital_observe_capital_demand",
            {
                **scope,
                "demand_id": demand.demand_id,
                "idempotency_key": demand.idempotency_key,
                "destination": _location(demand.destination),
                "observed_shortfall": str(demand.observed_shortfall),
                "observed_at_unix_nanos": _nanos(demand.observed_at),
                "required_by_unix_nanos": _nanos(demand.required_by),
                "expires_at_unix_nanos": _nanos(demand.expires_at),
                "priority": demand.priority.value,
                "confidence_bps": int(demand.confidence * 10_000),
                "account_watermark": demand.account_watermark,
                "risk_watermark": demand.risk_watermark,
                "destination_lease_fence": demand.destination_lease_fence,
                "causal_references": list(demand.causal_references),
            },
        )

    def availability(
        self, *, capital_group_id: str, location: FundingLocation
    ) -> CapitalAvailability:
        value = self._call(
            "capital_query_capital_availability",
            {
                "request_id": f"capital.availability:{time.time_ns()}",
                "capital_group_id": capital_group_id,
                "location": _location(location),
            },
        )
        return CapitalAvailability(
            capital_group_id=str(value["capital_group_id"]),
            readiness=CapitalReadiness(str(value["readiness"])),
            location=location,
            policy_minimum=Decimal(str(value["policy_minimum"])),
            policy_default_target=Decimal(str(value["policy_default_target"])),
            policy_maximum=Decimal(str(value["policy_maximum"])),
            policy_version=_required_int(value, "policy_version"),
            active_objective_ids=_string_tuple(value, "active_objective_ids"),
            active_demand_ids=_string_tuple(value, "active_demand_ids"),
            desired_target=Decimal(str(value["desired_target"])),
            observed_available=Decimal(str(value["observed_available"])),
            effective_target=Decimal(str(value["effective_target"])),
            deficit=Decimal(str(value["deficit"])),
            account_watermark=_required_int(value, "account_watermark"),
            risk_policy_version=_required_int(value, "risk_policy_version"),
            risk_watermark=_required_int(value, "risk_watermark"),
            reason=None if value.get("reason") is None else str(value["reason"]),
        )

    def reconcile_plan(
        self,
        *,
        capital_group_id: str,
        plan_id: str,
        request_id: str | None = None,
    ) -> dict[str, object]:
        if not capital_group_id.strip() or not plan_id.strip():
            raise ValueError("Capital group and plan identities are required")
        return self._call(
            "capital_reconcile_capital_plan",
            {
                "request_id": request_id or f"capital.reconcile:{time.time_ns()}",
                "capital_group_id": capital_group_id,
                "plan_id": plan_id,
                "observed_at_unix_nanos": time.time_ns(),
            },
        )

    def _call(self, method: str, body: dict[str, object]) -> dict[str, object]:
        return self._client.call(method, [body])


def _location(value: object) -> dict[str, str]:
    return {
        "broker": str(getattr(value, "broker")),
        "account_id": str(getattr(value, "account_id")),
        "segment": str(getattr(value, "segment")),
        "asset": str(getattr(value, "asset")),
    }


def _nanos(value: object) -> int:
    return int(getattr(value, "timestamp")() * 1_000_000_000)


def _required_int(value: dict[str, object], field: str) -> int:
    raw = value.get(field)
    if isinstance(raw, bool) or not isinstance(raw, int | str):
        raise ValueError(f"Capital response field {field!r} must be an integer")
    return int(raw)


def _string_tuple(value: dict[str, object], field: str) -> tuple[str, ...]:
    raw = value.get(field)
    if not isinstance(raw, list):
        raise ValueError(f"Capital response field {field!r} must be an array")
    if not all(isinstance(item, str) for item in raw):
        raise ValueError(f"Capital response field {field!r} must contain strings")
    return tuple(raw)
