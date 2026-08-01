from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping, Protocol

from kairospy.application.domain.account import LivePrivateStreamState
from kairospy.core.execution import ExecutionCoordinator, ExecutionStateSnapshot


@dataclass(frozen=True, slots=True)
class LiveRuntimeStateSnapshot:
    execution: ExecutionStateSnapshot
    private_stream: LivePrivateStreamState

    @classmethod
    def capture(
        cls,
        coordinator: ExecutionCoordinator,
        private_stream: LivePrivateStreamState,
    ) -> "LiveRuntimeStateSnapshot":
        return cls(ExecutionStateSnapshot.capture(coordinator), private_stream)

    def restore_into(
        self,
        coordinator: ExecutionCoordinator,
        private_stream: LivePrivateStreamState,
    ) -> None:
        self.execution.restore_into(coordinator)
        private_stream._seen_order_updates = set(self.private_stream._seen_order_updates)
        private_stream._seen_trade_updates = set(self.private_stream._seen_trade_updates)
        private_stream._order_timestamps = dict(self.private_stream._order_timestamps)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "execution": self.execution.to_dict(),
            "private_stream": self.private_stream.snapshot(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "LiveRuntimeStateSnapshot":
        return cls(
            ExecutionStateSnapshot.from_dict(_mapping(value.get("execution"))),
            LivePrivateStreamState.from_snapshot(_mapping(value.get("private_stream"))),
        )


class LiveRuntimeStateStore(Protocol):
    def load(self) -> LiveRuntimeStateSnapshot | None:
        ...

    def save(
        self,
        coordinator: ExecutionCoordinator,
        private_stream: LivePrivateStreamState,
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
        coordinator: ExecutionCoordinator,
        private_stream: LivePrivateStreamState,
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
