"""Execution owner-scoped LMDB indexed current-view contract."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

from kairospy.infrastructure.protocol.generated import kairos as _generated_kairos
from kairospy.infrastructure.transport.indexed_view import (
    IndexedViewMetadata,
    IndexedViewReader,
    IndexedViewSchema,
)

sys.modules.setdefault("kairos", _generated_kairos)

ORDERS_DATABASE = "orders"
INTENTS_DATABASE = "intents"
ALGORITHM_RUNS_DATABASE = "algorithm_runs"
COMMITMENTS_DATABASE = "commitments"
RISK_RESERVATIONS_DATABASE = "risk_reservations"
UNKNOWN_REMOTE_ORDERS_DATABASE = "unknown_remote_orders"
EXECUTION_RESOURCE_EPOCH = 1
EXECUTION_MAP_SIZE = 128 * 1024 * 1024
_ENTITY_KEY_VERSION = 1
_ENTITY_PREFIX = bytes((_ENTITY_KEY_VERSION,))

EXECUTION_INDEXED_SCHEMAS = (
    IndexedViewSchema(ORDERS_DATABASE, 1, "EOR3", 1),
    IndexedViewSchema(INTENTS_DATABASE, 1, "EIN3", 1),
    IndexedViewSchema(ALGORITHM_RUNS_DATABASE, 1, "EAR3", 1),
    IndexedViewSchema(COMMITMENTS_DATABASE, 1, "ECO3", 1),
    IndexedViewSchema(RISK_RESERVATIONS_DATABASE, 1, "ERR3", 1),
    IndexedViewSchema(UNKNOWN_REMOTE_ORDERS_DATABASE, 1, "EUR3", 1),
)

_ROOTS: dict[str, tuple[bytes, str]] = {
    ORDERS_DATABASE: (b"EOR3", "ExecutionOrderCurrent"),
    INTENTS_DATABASE: (b"EIN3", "ExecutionIntentCurrent"),
    ALGORITHM_RUNS_DATABASE: (b"EAR3", "ExecutionAlgorithmRunCurrent"),
    COMMITMENTS_DATABASE: (b"ECO3", "ExecutionCommitmentCurrent"),
    RISK_RESERVATIONS_DATABASE: (b"ERR3", "ExecutionRiskReservationCurrent"),
    UNKNOWN_REMOTE_ORDERS_DATABASE: (b"EUR3", "ExecutionUnknownRemoteOrderCurrent"),
}


def execution_indexed_environment_path(root: str | Path) -> Path:
    return (
        Path(root)
        / "views"
        / "v3"
        / "Execution"
        / "execution-main"
        / "epoch-1"
        / "current.lmdb"
    )


def indexed_entity_key(value: str) -> bytes:
    encoded = value.encode()
    if not value or value.strip() != value or b"\0" in encoded:
        raise ValueError(
            "indexed current-view identity must be non-empty and trimmed"
        )
    if len(encoded) > 0xFFFF:
        raise ValueError("indexed current-view identity is too long")
    return bytes((_ENTITY_KEY_VERSION,)) + len(encoded).to_bytes(2, "big") + encoded


def indexed_entity_identity(key: bytes) -> str:
    if len(key) < 3 or key[0] != _ENTITY_KEY_VERSION:
        raise ValueError("invalid Execution indexed entity key version")
    length = int.from_bytes(key[1:3], "big")
    if len(key) != length + 3:
        raise ValueError("invalid Execution indexed entity key length")
    return key[3:].decode()


def decode_indexed_value(payload: bytes, database: str) -> Any:
    identifier, root_name = _ROOTS[database]
    if len(payload) < 8 or payload[4:8] != identifier:
        raise ValueError(
            f"invalid Execution indexed value identifier for database {database}"
        )
    module = __import__(
        f"kairospy.infrastructure.protocol.generated.kairos.execution.v2.{root_name}",
        fromlist=[root_name],
    )
    return getattr(module, root_name).GetRootAs(payload, 0)


class ExecutionIndexedViewReader:
    def __init__(
        self,
        root: str | Path,
        *,
        workspace_id: str,
        launch_id: str | None,
        instance_id: str | None,
    ) -> None:
        self._reader = IndexedViewReader(
            execution_indexed_environment_path(root),
            map_size=EXECUTION_MAP_SIZE,
            workspace_id=workspace_id,
            launch_id=launch_id,
            instance_id=instance_id,
            owner="Execution",
            publisher_resource_id="execution-main",
            resource_epoch=EXECUTION_RESOURCE_EPOCH,
            schemas=EXECUTION_INDEXED_SCHEMAS,
        )

    def metadata(self) -> IndexedViewMetadata:
        return self._reader.metadata()

    def ensure_ready(self) -> IndexedViewMetadata:
        metadata = self.metadata()
        if metadata.rebuild_state != "ready":
            raise RuntimeError("Execution indexed current view is not ready")
        return metadata

    def get(self, database: str, identity: str) -> Any | None:
        self.ensure_ready()
        key = indexed_entity_key(identity)
        payload = self._reader.get(database, key)
        if payload is None:
            return None
        value = decode_indexed_value(payload, database)
        _validate_identity(database, identity, value)
        return value

    def values(self, database: str) -> tuple[Any, ...]:
        self.ensure_ready()
        rows = self._reader.prefix(database, _ENTITY_PREFIX, limit=sys.maxsize)
        values: list[Any] = []
        for key, payload in rows:
            identity = indexed_entity_identity(key)
            value = decode_indexed_value(payload, database)
            _validate_identity(database, identity, value)
            values.append(value)
        return tuple(values)

    def close(self) -> None:
        self._reader.close()


def _validate_identity(database: str, expected: str, value: Any) -> None:
    state = value.State()
    if state is None:
        raise ValueError(f"Execution indexed value in {database} has no state")
    if database == ORDERS_DATABASE or database == COMMITMENTS_DATABASE:
        actual = _required_text(state.OrderId(), "order_id")
    elif database == INTENTS_DATABASE:
        intent = state.Intent()
        if intent is None:
            raise ValueError("Execution indexed intent has no intent payload")
        actual = _required_text(intent.IntentId(), "intent_id")
    elif database == ALGORITHM_RUNS_DATABASE:
        actual = _required_text(state.AlgorithmRunId(), "algorithm_run_id")
    elif database == RISK_RESERVATIONS_DATABASE:
        actual = _required_text(state.ReservationId(), "reservation_id")
    else:
        actual = _required_text(state.RemoteOrderId(), "remote_order_id")
    if actual != expected:
        raise ValueError(
            f"Execution indexed key/value identity mismatch: key={expected}, value={actual}"
        )


def _required_text(value: bytes | None, field: str) -> str:
    if value is None:
        raise ValueError(f"Execution indexed value is missing {field}")
    return value.decode()


__all__ = [
    "ALGORITHM_RUNS_DATABASE",
    "COMMITMENTS_DATABASE",
    "EXECUTION_INDEXED_SCHEMAS",
    "EXECUTION_MAP_SIZE",
    "EXECUTION_RESOURCE_EPOCH",
    "ExecutionIndexedViewReader",
    "INTENTS_DATABASE",
    "ORDERS_DATABASE",
    "RISK_RESERVATIONS_DATABASE",
    "UNKNOWN_REMOTE_ORDERS_DATABASE",
    "decode_indexed_value",
    "execution_indexed_environment_path",
    "indexed_entity_identity",
    "indexed_entity_key",
]
