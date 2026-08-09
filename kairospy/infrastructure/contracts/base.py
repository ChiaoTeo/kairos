"""Common Python contract transport primitives."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kairospy.infrastructure.transport.shared_snapshot import SharedSnapshotReader


@dataclass(frozen=True, slots=True)
class SnapshotMetadata:
    """Common metadata carried by every module snapshot contract."""

    snapshot_id: str | None
    view_key: str | None
    producer_id: str | None
    event_stream_id: str | None
    generation: int
    event_sequence: int
    published_at_unix_nanos: int

    def __post_init__(self) -> None:
        if self.generation < 0 or self.event_sequence < 0 or self.published_at_unix_nanos < 0:
            raise ValueError("snapshot watermark fields cannot be negative")


@dataclass(frozen=True, slots=True)
class ContractSnapshot:
    """Stable KSS1 payload plus typed contract metadata."""

    metadata: SnapshotMetadata
    payload: bytes

    @property
    def generation(self) -> int:
        return self.metadata.generation

    @property
    def event_sequence(self) -> int:
        return self.metadata.event_sequence


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


class MmapSnapshotReader:
    """Contract-owned typed transport entry point for one mmap view."""

    def __init__(
        self,
        path: str | Path,
        *,
        file_identifier: bytes | None = None,
        root_type: Any | None = None,
    ) -> None:
        self.path = Path(path)
        self.file_identifier = file_identifier
        self.root_type = root_type

    def read(self) -> ContractSnapshot:
        value = SharedSnapshotReader(self.path).read()
        if self.file_identifier is not None:
            if len(value.payload) < 8 or value.payload[4:8] != self.file_identifier:
                raise ValueError(
                    f"invalid contract payload identifier for {self.path}: "
                    f"expected {self.file_identifier!r}"
                )
        metadata = SnapshotMetadata(
            snapshot_id=None,
            view_key=None,
            producer_id=None,
            event_stream_id=None,
            generation=value.generation,
            event_sequence=0,
            published_at_unix_nanos=0,
        )
        if self.root_type is not None:
            root = self.root_type.GetRootAs(value.payload, 0)
            header = root.Header()
            if header is None:
                raise ValueError(f"snapshot header is missing for {self.path}")

            def text(value: bytes | None) -> str | None:
                return None if value is None else value.decode("utf-8")

            metadata = SnapshotMetadata(
                snapshot_id=text(header.SnapshotId()),
                view_key=text(header.ViewKey()),
                producer_id=text(header.OwnerActorId()),
                event_stream_id=text(header.EventStreamId()),
                generation=header.Generation() or value.generation,
                event_sequence=header.EventSequence(),
                published_at_unix_nanos=header.GeneratedAtUnixNanos(),
            )
        return ContractSnapshot(metadata=metadata, payload=value.payload)


__all__ = [
    "CommandEnvelope", "ContractSnapshot", "MmapSnapshotReader", "QueryEnvelope", "SnapshotMetadata",
]
