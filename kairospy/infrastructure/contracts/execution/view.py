"""Execution v2 active mmap views."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import sys
from typing import Any

from kairospy.infrastructure.transport.generated import kairos as _generated_kairos
from kairospy.infrastructure.transport.shared_snapshot import SharedSnapshotReader

sys.modules.setdefault("kairos", _generated_kairos)


class ExecutionViewKind(str, Enum):
    ACTIVE_ORDERS = "active-orders"
    ACTIVE_INTENTS = "active-intents"
    CURRENT_EXECUTION = "current-execution"


@dataclass(frozen=True, slots=True)
class ExecutionViewKey:
    workspace_id: str
    kind: ExecutionViewKind
    launch_id: str | None = None
    instance_id: str | None = None

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("view workspace identity is incomplete")

    def canonical_key(self) -> str:
        return (
            f"workspace={self.workspace_id};launch={self.launch_id or ''};"
            f"instance={self.instance_id or ''};view={self.kind.value}"
        )

    def resource_path(self, root: str | Path) -> Path:
        return (
            Path(root)
            / "execution"
            / "views"
            / _component(self.workspace_id)
            / self.kind.value
            / "current.snapshot"
        )


@dataclass(frozen=True, slots=True)
class ExecutionViewFrame:
    key: ExecutionViewKey
    generation: int
    payload: bytes
    value: Any


_VIEW_ROOTS: dict[ExecutionViewKind, tuple[bytes, str]] = {
    ExecutionViewKind.ACTIVE_ORDERS: (b"ECO2", "ActiveOrdersView"),
    ExecutionViewKind.ACTIVE_INTENTS: (b"ECI2", "ActiveIntentsView"),
    ExecutionViewKind.CURRENT_EXECUTION: (b"ECV2", "CurrentExecutionView"),
}


class ExecutionViewReader:
    def __init__(
        self, root: str | Path, key: ExecutionViewKey, *, retries: int = 8
    ) -> None:
        self.root = Path(root)
        self.key = key
        self._reader = SharedSnapshotReader(
            key.resource_path(self.root), retries=retries
        )

    def read(self) -> ExecutionViewFrame:
        snapshot = self._reader.read()
        value = decode_view(snapshot.payload, self.key.kind)
        metadata = value.Metadata()
        if metadata is None:
            raise ValueError("Execution v2 view metadata is missing")
        if _text(metadata.ViewKey()) != self.key.canonical_key():
            raise ValueError("Execution view key identity mismatch")
        if int(metadata.ResourceEpoch()) != 1:
            raise ValueError("unsupported Execution view resource epoch")
        if int(metadata.Generation()) != snapshot.generation:
            raise ValueError("Execution envelope and payload generation differ")
        if int(metadata.AppliedRevision() or 0) != snapshot.applied_event_sequence:
            raise ValueError("Execution envelope and payload event sequence differ")
        return ExecutionViewFrame(
            self.key, snapshot.generation, snapshot.payload, value
        )


def decode_view(payload: bytes, kind: ExecutionViewKind) -> Any:
    identifier, root_name = _VIEW_ROOTS[kind]
    if len(payload) < 8 or payload[4:8] != identifier:
        raise ValueError(f"invalid Execution {kind.value} view identifier")
    module = __import__(
        f"kairospy.infrastructure.transport.generated.kairos.execution.v2.{root_name}",
        fromlist=[root_name],
    )
    return getattr(module, root_name).GetRootAs(payload, 0)


def _component(value: str) -> str:
    return "".join(
        chr(byte)
        if (byte < 128 and chr(byte).isalnum()) or byte in b"-_."
        else f"%{byte:02X}"
        for byte in value.encode()
    )


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode()


__all__ = [
    "ExecutionViewFrame",
    "ExecutionViewKey",
    "ExecutionViewKind",
    "ExecutionViewReader",
    "decode_view",
]
