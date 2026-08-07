from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path


class JsonlLifecycleJournal:
    """Small composition-selected journal for one strategy instance."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: object) -> None:
        value = asdict(record) if is_dataclass(record) else record
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, default=str, separators=(",", ":")) + "\n")
