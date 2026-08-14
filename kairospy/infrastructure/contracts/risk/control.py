"""Risk v2 low-frequency control contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from kairospy.infrastructure.transport.commands import UnixJsonCommandClient


class RiskControlClient:
    """JSON-over-Unix control client for the Risk process boundary."""

    def __init__(self, socket_path: str | Path, *, timeout: float = 5.0) -> None:
        self._client = UnixJsonCommandClient(socket_path, timeout=timeout)

    def health(self) -> Mapping[str, Any]:
        return self.request("GET", "/v1/health")

    def advance_time(self, event_time_unix_nanos: int) -> Mapping[str, Any]:
        return self.request(
            "POST",
            "/v1/time/advance",
            {"event_time_unix_nanos": event_time_unix_nanos},
        )

    def request(
        self,
        method: str,
        path: str,
        body: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        status, value = self._client.request(method, path, body)
        if status >= 400:
            raise RuntimeError(
                str(value.get("error", f"Risk request failed: HTTP {status}"))
            )
        return value


__all__ = ["RiskControlClient"]
