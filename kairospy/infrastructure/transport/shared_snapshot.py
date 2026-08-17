"""Stable Python facade over the Rust KSS envelope reader."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SharedSnapshotPayload:
    envelope_version: int
    resource_epoch: int
    producer_incarnation: int
    generation: int
    applied_event_sequence: int
    published_at_unix_nanos: int
    payload: bytes


class SharedSnapshotReader:
    """Read owned, consistency-checked bytes through ``kairos-transport``."""

    def __init__(self, path: str | Path, *, retries: int | None = None) -> None:
        if retries is not None and retries < 1:
            raise ValueError("retries must be positive")
        # Source checkouts may be imported without a compiled extension. The
        # first transport use is intentionally fail-fast and never falls back
        # to a Python mmap implementation.
        from .native import native

        self.path = Path(path)
        self._reader: Any = native.SnapshotReader(self.path)

    def read(self) -> SharedSnapshotPayload:
        frame = self._reader.read()
        return SharedSnapshotPayload(
            envelope_version=int(frame.envelope_version),
            resource_epoch=int(frame.resource_epoch),
            producer_incarnation=int(frame.producer_incarnation),
            generation=int(frame.generation),
            applied_event_sequence=int(frame.applied_event_sequence),
            published_at_unix_nanos=int(frame.published_at_unix_nanos),
            payload=bytes(frame.payload),
        )

    def close(self) -> None:
        self._reader.close()

    def __enter__(self) -> SharedSnapshotReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = ["SharedSnapshotPayload", "SharedSnapshotReader"]
