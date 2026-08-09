"""Account contract facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .base import CommandEnvelope, MmapSnapshotReader, QueryEnvelope
from kairospy.infrastructure.transport.commands import UnixJsonCommandClient


class AccountContractClient:
    """Low-frequency Account query and command facade."""

    def __init__(self, socket_path: str | Path, *, timeout: float = 5.0) -> None:
        self._client = UnixJsonCommandClient(socket_path, timeout=timeout)

    def health(self) -> Mapping[str, Any]:
        return self._get("/v1/health")

    def capabilities(self) -> Mapping[str, Any]:
        return self._get("/v1/capabilities")

    def balances(self, *, symbol: str | None = None) -> Mapping[str, Any]:
        return self._get("/v1/balances", symbol=symbol)

    def positions(self, *, symbol: str | None = None) -> Mapping[str, Any]:
        return self._get("/v1/positions", symbol=symbol)

    def plan_order(self, command: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._post("/v1/plan-order", command)

    def publish_order_event(self, event: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._post("/v1/order-event", event)

    def publish_fill(self, fill: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._post("/v1/fill", fill)

    def _get(self, path: str, **params: object) -> Mapping[str, Any]:
        query = "&".join(f"{key}={value}" for key, value in params.items() if value is not None)
        status, value = self._client.request("GET", f"{path}?{query}" if query else path)
        return _response(status, value)

    def _post(self, path: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        status, value = self._client.request("POST", path, body)
        return _response(status, value)


def _response(status: int, value: Mapping[str, Any]) -> Mapping[str, Any]:
    if status >= 400:
        raise RuntimeError(str(value.get("error", f"Account request failed: HTTP {status}")))
    return value


def snapshot_reader(path: str | Path) -> MmapSnapshotReader:
    from kairospy.infrastructure.transport.generated.kairos.account.v1.AccountsSnapshot import AccountsSnapshot

    return MmapSnapshotReader(path, file_identifier=b"AAC1", root_type=AccountsSnapshot)


__all__ = ["AccountContractClient", "CommandEnvelope", "QueryEnvelope", "snapshot_reader"]
