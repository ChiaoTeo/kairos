from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping, Protocol

from kairospy.application.usecases.account.application.private_stream import PrivateStreamCheckpoint
from kairospy.application.usecases.execution.application.state import ExecutionStateOwner, ExecutionStateSnapshot


@dataclass(frozen=True, slots=True)
class LiveRuntimeStateSnapshot:
    execution: ExecutionStateSnapshot
    private_stream: PrivateStreamCheckpoint

    @classmethod
    def capture(
        cls,
        coordinator: ExecutionStateOwner,
        private_stream: PrivateStreamCheckpoint,
    ) -> "LiveRuntimeStateSnapshot":
        return cls(ExecutionStateSnapshot.capture(coordinator), private_stream)

    def restore_execution_into(self, coordinator: ExecutionStateOwner) -> None:
        self.execution.restore_into(coordinator)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "execution": self.execution.to_dict(),
            "private_stream": self.private_stream.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "LiveRuntimeStateSnapshot":
        return cls(
            ExecutionStateSnapshot.from_dict(_mapping(value.get("execution"))),
            PrivateStreamCheckpoint.from_dict(_mapping(value.get("private_stream"))),
        )


class LiveRuntimeStateStore(Protocol):
    def load(self) -> LiveRuntimeStateSnapshot | None:
        ...

    def save(
        self,
        coordinator: ExecutionStateOwner,
        private_stream: PrivateStreamCheckpoint,
    ) -> LiveRuntimeStateSnapshot:
        ...


@dataclass(frozen=True, slots=True)
class JsonLiveRuntimeStateStore:
    path: Path | str

    def load(self) -> LiveRuntimeStateSnapshot | None:
        path = Path(self.path)
        if not path.exists():
            return None
        return LiveRuntimeStateSnapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(
        self,
        coordinator: ExecutionStateOwner,
        private_stream: PrivateStreamCheckpoint,
    ) -> LiveRuntimeStateSnapshot:
        snapshot = LiveRuntimeStateSnapshot.capture(coordinator, private_stream)
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot.to_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return snapshot


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("live runtime state item must be a mapping")
    return value


__all__ = ["JsonLiveRuntimeStateStore", "LiveRuntimeStateSnapshot", "LiveRuntimeStateStore"]
