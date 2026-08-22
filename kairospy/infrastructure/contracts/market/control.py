"""Low-frequency Market v2 control contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from kairospy.infrastructure.transport.commands import UnixJsonRpcClient


class MarketControlClient:
    """JSON-over-Unix control client matching the Market OpenAPI contract."""

    def __init__(self, socket_path: str | Path, *, timeout: float = 5.0) -> None:
        self._client = UnixJsonRpcClient(socket_path, timeout=timeout)

    def health(self) -> Mapping[str, Any]:
        return self.call("market_health")

    def call(
        self,
        method: str,
        params: list[object] | None = None,
    ) -> Mapping[str, Any]:
        return self._client.call(method, params)

    def subscribe(self, request: Mapping[str, object]) -> Mapping[str, Any]:
        return self.call("market_subscribe", [request])

    def data_sources(self, query: Mapping[str, object] | None = None) -> Mapping[str, Any]:
        return self.call("market_data_sources", [dict(query or {})])

    def subscription(self, subscription_id: str) -> Mapping[str, Any]:
        return self.call("market_data_sources", [{"subscription_id": subscription_id}])

    def unsubscribe(
        self, subscription_id: str, *, headers: Mapping[str, str]
    ) -> Mapping[str, Any]:
        command = dict(headers)
        command["subscription_id"] = subscription_id
        return self.call("market_unsubscribe", [command])

    def release_owner(self, request: Mapping[str, object]) -> Mapping[str, Any]:
        return self.call("market_release_owner", [request])

    def recover(self, request: Mapping[str, object] | None = None) -> Mapping[str, Any]:
        return self.call("market_recover", [dict(request or {})])

    def pause_replay(self) -> Mapping[str, Any]:
        return self.call("market_pause_replay")

    def resume_replay(self) -> Mapping[str, Any]:
        return self.call("market_resume_replay")


__all__ = ["MarketControlClient"]
