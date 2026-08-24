from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Mapping


class StrategyDecisionJournal:
    """Append-only Strategy-owned decision and effect journal."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = None if path is None else Path(path)
        self._memory: list[dict[str, object]] = []
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: Mapping[str, object]) -> None:
        if self.path is None:
            self._memory.append(dict(record))
            return
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(dict(record), separators=(",", ":"), ensure_ascii=False)
                + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())

    def records(self) -> Iterable[dict[str, object]]:
        if self.path is None:
            return tuple(dict(value) for value in self._memory)
        if not self.path.is_file():
            return ()
        values: list[dict[str, object]] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(
                    f"strategy decision journal line {line_number} must be an object"
                )
            values.append(value)
        return tuple(values)


__all__ = ["StrategyDecisionJournal"]
