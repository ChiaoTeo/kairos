"""Typed clients for already-running business processes.

These clients are part of the System boundary. They expose health and control
commands through Unix REST; business state is read from module-owned typed
mmap views.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .supervisor import UnixRestClient


@dataclass(frozen=True, slots=True)
class SystemRestClient:
    """Synchronous typed facade over the asynchronous Unix REST transport."""

    socket_path: Path
    timeout: float = 3.0

    def __post_init__(self) -> None:
        if not isinstance(self.socket_path, Path):
            object.__setattr__(self, "socket_path", Path(self.socket_path))
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")

    def request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | bytes | None = None,
    ) -> dict[str, Any]:
        if method == "GET" and path != "/v1/health":
            raise ValueError(
                "GET /v1/health is the only REST query; read state from typed mmap views"
            )
        if isinstance(body, Mapping):
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        else:
            payload = body
        import asyncio

        return asyncio.run(
            UnixRestClient(self.socket_path, timeout=self.timeout).request(
                method, path, payload
            )
        )

    def status(self) -> dict[str, Any]:
        return self.request("GET", "/v1/health")

    def refresh(self) -> dict[str, Any]:
        return self.request("POST", "/v1/refresh")

    def stop(self) -> dict[str, Any]:
        return self.request("POST", "/v1/stop")

    def subscribe(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/v1/subscribe", body)

    def unsubscribe(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/v1/unsubscribe", body)

    def recover(self) -> dict[str, Any]:
        return self.request("POST", "/v1/recover")

    def command(self, component: str, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"/v1/{component}/command", body)


class AccountSystemClient(SystemRestClient):
    def reconcile(self) -> dict[str, Any]:
        return self.request("POST", "/v1/reconcile")


class ExecutionSystemClient(SystemRestClient):
    def submit_intent(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/v1/intents", intent)

    def cancel_intent(self, intent_id: str, *, reason: str = "") -> dict[str, Any]:
        return self.request(
            "POST", "/v1/intents/cancel", {"intent_id": intent_id, "reason": reason}
        )

    def expire_intent(self, intent_id: str, *, reason: str = "") -> dict[str, Any]:
        return self.request(
            "POST", "/v1/intents/expire", {"intent_id": intent_id, "reason": reason}
        )

    def submit(
        self, request: Mapping[str, Any], *, dry_run: bool = False
    ) -> dict[str, Any]:
        return self.request(
            "POST", "/v1/preview-submit" if dry_run else "/v1/intents", request
        )

    def cancel(self, order_id: str, reason: str = "system cancel") -> dict[str, Any]:
        return self.request("DELETE", f"/v1/orders/{order_id}", {"reason": reason})

    def replace(self, order_id: str, replacement: Mapping[str, Any]) -> dict[str, Any]:
        return self.request("PATCH", f"/v1/orders/{order_id}", replacement)


class MarketSystemClient(SystemRestClient):
    def subscribe(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/v1/subscribe", request)

    def unsubscribe(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/v1/unsubscribe", request)

    def recover(self) -> dict[str, Any]:
        return self.request("POST", "/v1/recover")


class RiskSystemClient(SystemRestClient):
    def configure(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/v1/configure", request)

    def assess(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/v1/assess", request)

    def reserve(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/v1/reserve", request)

    def release(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/v1/release", request)

    def consume(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/v1/consume", request)


class ReferenceSystemClient(SystemRestClient):
    def publish(self) -> dict[str, Any]:
        return self.request("POST", "/v1/publish")

    def add_asset(self, asset: Mapping[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/v1/assets", asset)


__all__ = [
    "SystemRestClient",
    "AccountSystemClient",
    "ExecutionSystemClient",
    "MarketSystemClient",
    "RiskSystemClient",
    "ReferenceSystemClient",
]
