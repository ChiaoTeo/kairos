"""Generic reader for the Kairos KSS1 double-slot snapshot envelope."""

from __future__ import annotations

from dataclasses import dataclass
import mmap
from pathlib import Path
import struct


@dataclass(frozen=True, slots=True)
class SharedSnapshotPayload:
    generation: int
    payload: bytes


class SharedSnapshotReader:
    """Read one stable payload from a Rust ``SharedSnapshotWriter`` file."""

    _MAGIC = b"KSS1"
    _FORMAT_VERSION = 1
    _HEADER_SIZE = 64
    _SLOT_COUNT = 2
    _ACTIVE_OFFSET = 12
    _SLOT_LENGTH_OFFSET = 24
    _SLOT_GENERATION_OFFSET = 32

    def __init__(self, path: str | Path, *, retries: int = 8) -> None:
        if retries < 1:
            raise ValueError("retries must be positive")
        self.path = Path(path)
        self.retries = retries

    def read(self) -> SharedSnapshotPayload:
        with self.path.open("rb") as file:
            with mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
                return self._read_mapped(mapped)

    def _read_mapped(self, mapped: mmap.mmap) -> SharedSnapshotPayload:
        if len(mapped) < self._HEADER_SIZE or mapped[:4] != self._MAGIC:
            raise ValueError("invalid shared snapshot header")
        version, slots, slot_size = struct.unpack_from("<HHI", mapped, 4)
        if (
            version != self._FORMAT_VERSION
            or slots != self._SLOT_COUNT
            or slot_size <= 0
        ):
            raise ValueError("unsupported shared snapshot layout")
        if len(mapped) < self._HEADER_SIZE + slots * slot_size:
            raise ValueError("truncated shared snapshot file")
        for _ in range(self.retries):
            active = mapped[self._ACTIVE_OFFSET]
            if active >= slots:
                raise ValueError("invalid active snapshot slot")
            length = struct.unpack_from(
                "<I", mapped, self._SLOT_LENGTH_OFFSET + active * 4
            )[0]
            generation = struct.unpack_from(
                "<Q", mapped, self._SLOT_GENERATION_OFFSET + active * 8
            )[0]
            if not 0 < length <= slot_size:
                raise ValueError("active snapshot slot is empty or too large")
            start = self._HEADER_SIZE + active * slot_size
            payload = bytes(mapped[start : start + length])
            active_after = mapped[self._ACTIVE_OFFSET]
            generation_after = struct.unpack_from(
                "<Q", mapped, self._SLOT_GENERATION_OFFSET + active * 8
            )[0]
            if active == active_after and generation == generation_after:
                return SharedSnapshotPayload(generation=generation, payload=payload)
        raise RuntimeError("shared snapshot changed while being read")


__all__ = ["SharedSnapshotPayload", "SharedSnapshotReader"]
