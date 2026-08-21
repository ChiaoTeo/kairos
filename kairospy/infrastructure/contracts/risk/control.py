"""Risk v2 low-frequency control contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from kairospy.infrastructure.transport.commands import UnixJsonRpcClient


class RiskControlClient:
    """JSON-over-Unix control client for the Risk process boundary."""

    def __init__(self, socket_path: str | Path, *, timeout: float = 5.0) -> None:
        self._client = UnixJsonRpcClient(socket_path, timeout=timeout)

    def health(self) -> Mapping[str, Any]:
        return self.call("risk_health")

    def advance_time(self, event_time_unix_nanos: int) -> Mapping[str, Any]:
        return self.call(
            "risk_advance_time",
            [{"event_time_unix_nanos": event_time_unix_nanos}],
        )

    def publish_policy(self, request: Mapping[str, object]) -> Mapping[str, Any]:
        return self.call("risk_publish_policy", [request])

    def pre_trade_check(self, request: Mapping[str, object]) -> Mapping[str, Any]:
        return self.call("risk_pre_trade_check", [request])

    def authorize_and_reserve(self, request: Mapping[str, object]) -> Mapping[str, Any]:
        return self.call("risk_authorize_and_reserve", [request])

    def release_reservation(self, request: Mapping[str, object]) -> Mapping[str, Any]:
        return self.call("risk_release_reservation", [request])

    def consume_reservation(self, request: Mapping[str, object]) -> Mapping[str, Any]:
        return self.call("risk_consume_reservation", [request])

    def call(
        self,
        method: str,
        params: list[object] | None = None,
    ) -> Mapping[str, Any]:
        return self._client.call(method, params)


__all__ = ["RiskControlClient"]
