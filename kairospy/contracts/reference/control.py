"""Low-frequency Reference control contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from kairospy.infrastructure.transport.commands import UnixJsonRpcClient


class ReferenceControlClient:
    """JSON-over-Unix control client matching the Reference process API."""

    def __init__(
        self,
        socket_path: str | Path,
        *,
        timeout: float = 5.0,
    ) -> None:
        self._client = UnixJsonRpcClient(socket_path, timeout=timeout)

    def health(self) -> Mapping[str, Any]:
        return self.call("reference_health")

    def status(self) -> Mapping[str, Any]:
        """Read the Reference-owned detailed runtime status."""

        return self.call("reference_status")

    def refresh(self, *, source: str | None = None) -> Mapping[str, Any]:
        return self.call("reference_refresh", [source])

    def publish(self) -> Mapping[str, Any]:
        return self.call("reference_publish")

    def add_asset(self, asset: Mapping[str, object]) -> Mapping[str, Any]:
        return self.call("reference_upsert_asset", [asset])

    def set_source_paused(self, source: str, paused: bool) -> Mapping[str, Any]:
        return self.call(
            "reference_pause_source" if paused else "reference_resume_source",
            [source],
        )

    def set_option_underlying(
        self, underlying: str, enabled: bool
    ) -> Mapping[str, Any]:
        return self.call(
            (
                "reference_add_option_coverage"
                if enabled
                else "reference_remove_option_coverage"
            ),
            [underlying],
        )

    def call(
        self,
        method: str,
        params: list[object] | None = None,
    ) -> Mapping[str, Any]:
        return self._client.call(method, params)


__all__ = ["ReferenceControlClient"]
