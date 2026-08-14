"""Low-frequency Market v2 control contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from kairospy.infrastructure.transport.commands import UnixJsonCommandClient


class MarketControlClient:
    """JSON-over-Unix control client matching the Market OpenAPI contract."""

    def __init__(self, socket_path: str | Path, *, timeout: float = 5.0) -> None:
        self._client = UnixJsonCommandClient(socket_path, timeout=timeout)

    def health(self) -> Mapping[str, Any]:
        return self._request("GET", "/v1/health")

    def request(
        self,
        method: str,
        path: str,
        body: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        return self._request(method, path, body)

    def subscribe(self, request: Mapping[str, object]) -> Mapping[str, Any]:
        """Establish a v2 subscription through ``POST /v1/subscriptions``."""

        return self._request("POST", "/v1/subscriptions", request)

    def subscription(self, subscription_id: str) -> Mapping[str, Any]:
        return self._request(
            "GET", f"/v1/subscriptions/{quote(subscription_id, safe='')}"
        )

    def unsubscribe(
        self, subscription_id: str, *, headers: Mapping[str, str]
    ) -> Mapping[str, Any]:
        status, value = self._client.request_with_headers(
            "DELETE",
            f"/v1/subscriptions/{quote(subscription_id, safe='')}",
            None,
            headers=headers,
        )
        if status >= 400:
            raise RuntimeError(str(value.get("error", f"Market request failed: HTTP {status}")))
        return value

    def release_owner(self, request: Mapping[str, object]) -> Mapping[str, Any]:
        return self._request("POST", "/v1/subscriptions/release-owner", request)

    def recover(self, request: Mapping[str, object]) -> Mapping[str, Any]:
        return self._request("POST", "/v1/recovery", request)

    def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        status, value = self._client.request(method, path, body)
        if status >= 400:
            raise RuntimeError(
                str(value.get("error", f"Market request failed: HTTP {status}"))
            )
        return value


__all__ = ["MarketControlClient"]
