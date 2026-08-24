from __future__ import annotations

from pathlib import Path
import time

from kairospy.infrastructure.transport.commands import UnixJsonRpcClient


class CapitalContractClient:
    """Low-frequency Strategy/Execution commands for one Capital process."""

    def __init__(self, socket_path: str | Path, *, timeout: float = 5.0) -> None:
        self._client = UnixJsonRpcClient(socket_path, timeout=timeout)

    def health(self) -> dict[str, object]:
        return self._client.call("capital_health")

    def publish_funding_objective_request(
        self, request: dict[str, object]
    ) -> dict[str, object]:
        return self._call("capital_publish_funding_objective", request)

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

    def cancel_funding_objective_request(
        self, request: dict[str, object]
    ) -> dict[str, object]:
        return self._call("capital_cancel_funding_objective", request)

    def observe_capital_demand_request(
        self, request: dict[str, object]
    ) -> dict[str, object]:
        return self._call("capital_observe_capital_demand", request)

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

    def reconcile_plan_request(self, request: dict[str, object]) -> dict[str, object]:
        return self._call("capital_reconcile_capital_plan", request)

    def _call(self, method: str, body: dict[str, object]) -> dict[str, object]:
        return self._client.call(method, [body])
