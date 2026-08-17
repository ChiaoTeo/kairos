"""Reference v2 typed current view backed by mmap."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

from kairospy.infrastructure.transport.generated import kairos as _generated_kairos
from kairospy.infrastructure.transport.shared_snapshot import SharedSnapshotReader

sys.modules.setdefault("kairos", _generated_kairos)


@dataclass(frozen=True, slots=True)
class ReferenceViewKey:
    actor_id: str = "reference-actor"

    def __post_init__(self) -> None:
        if not self.actor_id.strip():
            raise ValueError("Reference view actor_id is required")

    def resource_path(self, root: str | Path) -> Path:
        return (
            Path(root)
            / "reference"
            / self.actor_id
            / "latest"
            / "current.snapshot"
        )


@dataclass(frozen=True, slots=True)
class ReferenceViewFrame:
    generation: int
    event_sequence: int
    value: Any


class ReferenceViewReader:
    def __init__(
        self,
        root: str | Path,
        key: ReferenceViewKey | None = None,
        *,
        retries: int = 8,
    ) -> None:
        self.key = key or ReferenceViewKey()
        self._reader = SharedSnapshotReader(
            self.key.resource_path(root), retries=retries
        )

    def read(self) -> ReferenceViewFrame:
        snapshot = self._reader.read()
        payload = snapshot.payload
        if len(payload) < 8 or payload[4:8] != b"RFV2":
            raise ValueError("invalid Reference latest view identifier")
        module = __import__(
            "kairospy.infrastructure.transport.generated.kairos.reference.v2.ReferenceLatestView",
            fromlist=["ReferenceLatestView"],
        )
        value = module.ReferenceLatestView.GetRootAs(payload, 0)
        metadata = value.Metadata()
        if metadata is None:
            raise ValueError("Reference view metadata is missing")
        if int(metadata.Generation()) != snapshot.generation:
            raise ValueError("Reference mmap generation mismatch")
        event_sequence = int(metadata.AppliedRevision())
        if event_sequence != snapshot.applied_event_sequence:
            raise ValueError("Reference mmap event-sequence mismatch")
        if _text(metadata.OwnerId()) != self.key.actor_id:
            raise ValueError("Reference mmap actor identity mismatch")
        if int(metadata.Completeness()) != 1:
            raise ValueError("Reference mmap view is incomplete")
        return ReferenceViewFrame(snapshot.generation, event_sequence, value)


def text(value: bytes | None) -> str | None:
    return None if value is None else value.decode()


def decimal(value: Any | None) -> str | None:
    if value is None:
        return None
    mantissa = int(value.Mantissa())
    scale = int(value.Scale())
    if scale == 0:
        return str(mantissa)
    negative = mantissa < 0
    digits = str(abs(mantissa)).rjust(scale + 1, "0")
    return f"{'-' if negative else ''}{digits[:-scale]}.{digits[-scale:]}"


def lifecycle(value: int) -> str:
    return {
        1: "draft",
        2: "active",
        3: "trading",
        4: "suspended",
        5: "inactive",
        6: "retired",
        7: "expired",
    }.get(value, "unknown")


def _text(value: bytes | None) -> str | None:
    return text(value)


__all__ = [
    "ReferenceViewFrame",
    "ReferenceViewKey",
    "ReferenceViewReader",
    "decimal",
    "lifecycle",
    "text",
]
