"""Common Python contract transport primitives."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SnapshotMetadata:
    """Common metadata carried by every module snapshot contract."""

    snapshot_id: str | None
    view_key: str | None
    producer_id: str | None
    generation: int
    published_at_unix_nanos: int

    def __post_init__(self) -> None:
        if self.generation < 0 or self.published_at_unix_nanos < 0:
            raise ValueError("snapshot metadata fields cannot be negative")


@dataclass(frozen=True, slots=True)
class ContractSnapshot:
    """Stable KSS1 payload plus typed contract metadata."""

    metadata: SnapshotMetadata
    payload: bytes

    @property
    def generation(self) -> int:
        return self.metadata.generation


@dataclass(frozen=True, slots=True)
class CommandEnvelope:
    """Stable write-side envelope shared by module command facades."""

    command_type: str
    request_id: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class QueryEnvelope:
    """Stable read-side query envelope for low-frequency query transports."""

    query_type: str
    request_id: str
    payload: bytes


__all__ = [
    "CommandEnvelope",
    "ContractSnapshot",
    "QueryEnvelope",
    "SnapshotMetadata",
]
