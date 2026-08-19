from __future__ import annotations

from pathlib import Path
import time

from kairospy.application.capital.models import CapitalDemand, FundingObjective
from kairospy.infrastructure.transport.commands import UnixJsonCommandClient


class CapitalContractClient:
    """Low-frequency Strategy/Execution commands for one Capital process."""

    def __init__(self, socket_path: str | Path, *, timeout: float = 5.0) -> None:
        self._client = UnixJsonCommandClient(socket_path, timeout=timeout)

    def publish_funding_objective(
        self,
        objective: FundingObjective,
        **scope: object,
    ) -> dict[str, object]:
        return self._post(
            "/v1/objectives/publish",
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
                "observed_at_unix_nanos": time.time_ns(),
            },
        )

    def cancel_funding_objective(
        self, objective_id: str, *, expected_version: int, **scope: object
    ) -> dict[str, object]:
        return self._post(
            "/v1/objectives/cancel",
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
        return self._post(
            "/v1/demands/observe",
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

    def _post(self, path: str, body: dict[str, object]) -> dict[str, object]:
        status, value = self._client.request("POST", path, body)
        if status >= 400:
            raise RuntimeError(str(value.get("error", f"Capital HTTP {status}")))
        return value


def _location(value: object) -> dict[str, str]:
    return {
        "broker": str(getattr(value, "broker")),
        "account_id": str(getattr(value, "account_id")),
        "segment": str(getattr(value, "segment")),
        "asset": str(getattr(value, "asset")),
    }


def _nanos(value: object) -> int:
    return int(getattr(value, "timestamp")() * 1_000_000_000)
