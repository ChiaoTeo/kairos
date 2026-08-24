from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from ..domain.messages import LifecycleRecord


class StrategyLifecycleJournal:
    """Small composition-selected journal for one strategy instance."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: LifecycleRecord) -> None:
        value = asdict(record)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, default=str, separators=(",", ":")) + "\n")
