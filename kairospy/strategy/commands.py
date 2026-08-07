"""Versioned command transport contract for strategy capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from time import time_ns
from typing import Mapping


@dataclass(frozen=True, slots=True)
class CommandSource:
    stream_id: str | None = None
    sequence: int | None = None
    snapshot_id: str | None = None

    def as_dict(self) -> dict[str, object] | None:
        if self.stream_id is None and self.sequence is None and self.snapshot_id is None:
            return None
        return {
            "stream_id": self.stream_id,
            "sequence": self.sequence,
            "snapshot_id": self.snapshot_id,
        }


@dataclass(frozen=True, slots=True)
class CommandEnvelope:
    command_id: str
    operation: str
    strategy_id: str
    instance_id: str
    payload: Mapping[str, object]
    launch_id: str | None = None
    idempotency_key: str | None = None
    issued_at_unix_nanos: int | None = None
    source: CommandSource | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported command schema version")
        if not all((self.command_id.strip(), self.operation.strip(), self.strategy_id.strip(), self.instance_id.strip())):
            raise ValueError("command identity and operation are required")
        if self.issued_at_unix_nanos is not None and self.issued_at_unix_nanos < 0:
            raise ValueError("command timestamp cannot be negative")

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "command_id": self.command_id,
            "idempotency_key": self.idempotency_key or self.command_id,
            "issued_at_unix_nanos": self.issued_at_unix_nanos or time_ns(),
            "operation": self.operation,
            "strategy_id": self.strategy_id,
            "launch_id": self.launch_id,
            "instance_id": self.instance_id,
            "source": None if self.source is None else self.source.as_dict(),
            "payload": dict(self.payload),
        }
