from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trading.storage.codec import to_primitive


class PersistentEventLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ids = set()
        if self.path.exists():
            for row in self.read():
                self._ids.add(row["event_id"])

    def append(self, event_id: str, event_type: str, payload: Any) -> None:
        if event_id in self._ids:
            return
        row = {"schema_version": 1, "event_id": event_id, "event_type": event_type, "payload": to_primitive(payload)}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        self._ids.add(event_id)

    def read(self) -> tuple[dict[str, Any], ...]:
        if not self.path.exists():
            return ()
        return tuple(json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line)

    def find(self, event_id: str) -> dict[str, Any] | None:
        return next((row for row in self.read() if row["event_id"] == event_id), None)
