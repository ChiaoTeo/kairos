"""Low-frequency Execution v2 control contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from kairospy.infrastructure.transport.commands import UnixJsonCommandClient


class ExecutionControlClient:
    """JSON-over-UDS client matching ``schemas/v2/execution/control.openapi.yaml``."""

    def __init__(self, socket_path: str | Path, *, timeout: float = 30.0) -> None:
        self._client = UnixJsonCommandClient(socket_path, timeout=timeout)

    def health(self) -> Mapping[str, Any]:
        return self.request("GET", "/v1/health")

    def submit_intent(self, request: Mapping[str, object]) -> Mapping[str, Any]:
        return self.request("POST", "/v1/intents", request)

    def cancel_order(
        self, order_id: str, request: Mapping[str, object] | None = None
    ) -> Mapping[str, Any]:
        return self.request("DELETE", f"/v1/orders/{quote(order_id, safe='')}", request)

    def replace_order(
        self, order_id: str, request: Mapping[str, object]
    ) -> Mapping[str, Any]:
        return self.request("PATCH", f"/v1/orders/{quote(order_id, safe='')}", request)

    def reconcile(self, request: Mapping[str, object]) -> Mapping[str, Any]:
        return self.request("POST", "/v1/reconciliation", request)

    def request(
        self,
        method: str,
        path: str,
        body: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        status, value = self._client.request(method, path, body)
        if status >= 400:
            raise RuntimeError(
                str(value.get("error", f"Execution request failed: HTTP {status}"))
            )
        return value


__all__ = ["ExecutionControlClient"]
