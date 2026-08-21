"""Low-frequency Execution v2 control contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from kairospy.infrastructure.transport.commands import UnixJsonRpcClient


class ExecutionControlClient:
    """JSON-over-UDS client matching the Execution JSON-RPC control trait."""

    def __init__(self, socket_path: str | Path, *, timeout: float = 30.0) -> None:
        self._client = UnixJsonRpcClient(socket_path, timeout=timeout)

    def health(self) -> Mapping[str, Any]:
        return self.call("execution_health")

    def routes(self, query: Mapping[str, object] | None = None) -> Mapping[str, Any]:
        return self.call("execution_routes", [dict(query or {})])

    def submit_intent(self, request: Mapping[str, object]) -> Mapping[str, Any]:
        return self.call("execution_submit_intent", [request])

    def cancel_order(
        self, order_id: str, request: Mapping[str, object] | None = None
    ) -> Mapping[str, Any]:
        return self.call("execution_cancel_order", [order_id, request or {}])

    def replace_order(
        self, order_id: str, request: Mapping[str, object]
    ) -> Mapping[str, Any]:
        return self.call("execution_replace_order", [order_id, request])

    def reconcile(self, request: Mapping[str, object]) -> Mapping[str, Any]:
        return self.call("execution_reconcile", [request])

    def advance_time(self, event_time_unix_nanos: int) -> Mapping[str, Any]:
        return self.call(
            "execution_advance_time",
            [{"event_time_unix_nanos": int(event_time_unix_nanos)}],
        )

    def backtest_run(self, request: Mapping[str, object]) -> Mapping[str, Any]:
        return self.call("execution_backtest_run", [request])

    def backtest_market(self, event: Mapping[str, object]) -> Mapping[str, Any]:
        return self.call("execution_backtest_market", [{"event": event}])

    def call(
        self,
        method: str,
        params: list[object] | None = None,
    ) -> Mapping[str, Any]:
        return self._client.call(method, params)


__all__ = ["ExecutionControlClient"]
