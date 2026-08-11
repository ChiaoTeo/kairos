"""Typed clients for already-running business processes.

These clients are part of the System boundary.  They control or query a
process-owned application through its Unix REST socket; they never own
business state and they are not used by one-shot business CLIs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode

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

    def snapshot(self) -> dict[str, Any]:
        return self.request("GET", "/v1/snapshot")

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


def _query(path: str, values: Mapping[str, Any]) -> str:
    encoded = [(key, value) for key, value in values.items() if value is not None]
    return f"{path}?{urlencode(encoded, doseq=True)}" if encoded else path


class AccountSystemClient(SystemRestClient):
    def snapshot(self, symbol: str | None = None) -> dict[str, Any]:
        return self.request("GET", _query("/v1/snapshot", {"symbol": symbol}))

    def balances(
        self,
        *,
        segments: list[str] | None = None,
        include_zero: bool = False,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "GET",
            _query(
                "/v1/balances",
                {
                    "segment": segments or [],
                    "include_zero": str(include_zero).lower(),
                    "page": page,
                    "page_size": page_size,
                },
            ),
        )

    def positions(
        self, *, segments: list[str] | None = None, symbol: str | None = None
    ) -> dict[str, Any]:
        return self.request(
            "GET",
            _query("/v1/positions", {"segment": segments or [], "symbol": symbol}),
        )

    def open_orders(
        self, *, symbol: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        return self.request(
            "GET", _query("/v1/open-orders", {"symbol": symbol, "limit": limit})
        )

    def orders(self, *, order_id: str | None = None) -> dict[str, Any]:
        return self.request("GET", _query("/v1/orders", {"order_id": order_id}))

    def reconcile(self) -> dict[str, Any]:
        return self.request("POST", "/v1/reconcile")

    def market_profiles(self) -> dict[str, Any]:
        return self.request("GET", "/v1/market-profiles")

    def capabilities(self) -> dict[str, Any]:
        return self.request("GET", "/v1/capabilities")

    def fees(self) -> dict[str, Any]:
        return self.request("GET", "/v1/fees")


class ExecutionSystemClient(SystemRestClient):
    def intents(self) -> dict[str, Any]:
        return self.request("GET", "/v1/intents")

    def intent(self, intent_id: str) -> dict[str, Any]:
        return self.request("GET", _query("/v1/intent", {"intent_id": intent_id}))

    def intent_events(self, *, intent_id: str | None = None) -> dict[str, Any]:
        return self.request(
            "GET", _query("/v1/intent-events", {"intent_id": intent_id})
        )

    def submit_intent(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/v1/intents/submit", intent)

    def cancel_intent(self, intent_id: str, *, reason: str = "") -> dict[str, Any]:
        return self.request(
            "POST", "/v1/intents/cancel", {"intent_id": intent_id, "reason": reason}
        )

    def expire_intent(self, intent_id: str, *, reason: str = "") -> dict[str, Any]:
        return self.request(
            "POST", "/v1/intents/expire", {"intent_id": intent_id, "reason": reason}
        )

    def orders(self, *, account_id: str | None = None) -> dict[str, Any]:
        return self.request("GET", _query("/v1/orders", {"account_id": account_id}))

    def open_orders(self, *, account_id: str | None = None) -> dict[str, Any]:
        return self.request(
            "GET", _query("/v1/open-orders", {"account_id": account_id})
        )

    def history(self, *, account_id: str | None = None) -> dict[str, Any]:
        return self.request("GET", _query("/v1/history", {"account_id": account_id}))

    def remote_open_orders(self, *, symbol: str | None = None) -> dict[str, Any]:
        return self.request("GET", _query("/v1/remote-open-orders", {"symbol": symbol}))

    def remote_history(
        self, *, symbol: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        return self.request(
            "GET", _query("/v1/remote-history", {"symbol": symbol, "limit": limit})
        )

    def remote_order(self, order_id: str) -> dict[str, Any]:
        return self.request("GET", _query("/v1/remote-order", {"order_id": order_id}))

    def events(self, *, order_id: str | None = None) -> dict[str, Any]:
        return self.request("GET", _query("/v1/events", {"order_id": order_id}))

    def audit(self, **filters: Any) -> dict[str, Any]:
        return self.request("GET", _query("/v1/audit", filters))

    def submit(
        self, request: Mapping[str, Any], *, dry_run: bool = False
    ) -> dict[str, Any]:
        return self.request(
            "POST", "/v1/preview-submit" if dry_run else "/v1/submit", request
        )

    def cancel(self, order_id: str, reason: str = "system cancel") -> dict[str, Any]:
        return self.request(
            "POST", "/v1/cancel", {"order_id": order_id, "reason": reason}
        )

    def replace(self, order_id: str, replacement: Mapping[str, Any]) -> dict[str, Any]:
        return self.request(
            "POST", "/v1/replace", {"order_id": order_id, "replacement": replacement}
        )


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
