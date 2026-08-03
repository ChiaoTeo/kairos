from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Mapping


class OperationJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def append(
        self,
        action: str,
        *,
        actor: str = "cli",
        target: Mapping[str, object] | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "event_time": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "action": action,
            "target": dict(target or {}),
            "payload": _jsonable(payload or {}),
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, sort_keys=True) + "\n")
        return self.path


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


__all__ = ["OperationJournal"]
