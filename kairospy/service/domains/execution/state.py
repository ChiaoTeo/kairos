from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from kairospy.core.execution import ExecutionCoordinator, ExecutionStateSnapshot


@dataclass(frozen=True, slots=True)
class JsonExecutionStateStore:
    path: Path | str

    def load(self) -> ExecutionStateSnapshot | None:
        path = Path(self.path)
        if not path.exists():
            return None
        return ExecutionStateSnapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, coordinator: ExecutionCoordinator) -> ExecutionStateSnapshot:
        snapshot = ExecutionStateSnapshot.capture(coordinator)
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot.to_dict(), sort_keys=True, indent=2), encoding="utf-8")
        return snapshot


__all__ = ["JsonExecutionStateStore"]
