"""Read-side timeline application for persisted lifecycle/event records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True, slots=True)
class TimelineApplication:
    """Reads append-only JSONL records without owning the event stream."""

    def list(self, path: str | Path, *, limit: int | None = None) -> list[dict[str, Any]]:
        records = list(self._records(Path(path)))
        if limit is not None:
            if limit < 0:
                raise ValueError("limit must not be negative")
            records = records[-limit:] if limit else []
        return records

    def export(self, path: str | Path, destination: str | Path) -> Path:
        output = Path(destination)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as stream:
            for record in self._records(Path(path)):
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return output

    @staticmethod
    def _records(path: Path) -> Iterator[dict[str, Any]]:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"timeline record {line_number} must be an object")
                yield value


__all__ = ["TimelineApplication"]
