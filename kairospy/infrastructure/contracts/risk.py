"""Risk contract facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .base import CommandEnvelope, MmapSnapshotReader, QueryEnvelope
from kairospy.infrastructure.transport.commands import UnixJsonCommandClient


class RiskContractClient:
    """Low-frequency Risk health and authoritative command facade."""

    def __init__(self, socket_path: str | Path, *, timeout: float = 5.0) -> None:
        self._client = UnixJsonCommandClient(socket_path, timeout=timeout)

    def health(self) -> Mapping[str, Any]:
        return self._request("GET", "/v1/health")

    def authorize_and_reserve(self, command: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._request("POST", "/v1/authorize_and_reserve", command)

    def pre_trade_check(self, command: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._request("POST", "/v1/pre_trade_check", command)

    def post_trade_check(self, command: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._request("POST", "/v1/post_trade_check", command)

    def open_circuit(self, command: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._request("POST", "/v1/open_circuit", command)

    def close_circuit(self, command: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._request("POST", "/v1/close_circuit", command)

    def release(self, reservation_id: str, at_unix_nanos: int) -> Mapping[str, Any]:
        return self._request(
            "POST",
            "/v1/release",
            {"reservation_id": reservation_id, "at_unix_nanos": at_unix_nanos},
        )

    def consume(self, reservation_id: str, at_unix_nanos: int) -> Mapping[str, Any]:
        return self._request(
            "POST",
            "/v1/consume",
            {"reservation_id": reservation_id, "at_unix_nanos": at_unix_nanos},
        )

    def resize(self, command: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._request("POST", "/v1/resize", command)

    def _request(
        self, method: str, path: str, body: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        status, value = self._client.request(method, path, body)
        if status >= 400:
            raise RuntimeError(
                str(value.get("error", f"Risk request failed: HTTP {status}"))
            )
        return value


def snapshot_reader(path: str | Path) -> MmapSnapshotReader:
    from kairospy.infrastructure.transport.generated.kairos.risk.v1.RiskSnapshot import (
        RiskSnapshot,
    )

    return MmapSnapshotReader(path, file_identifier=b"PRK1", root_type=RiskSnapshot)


__all__ = ["CommandEnvelope", "QueryEnvelope", "RiskContractClient", "snapshot_reader"]
