"""Queries for lifecycle records persisted by one launch instance."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ...workspace import InstanceWorkspace


@dataclass(frozen=True, slots=True)
class LaunchInstanceTimelineApplication:
    """Reads the lifecycle artifact of one explicitly selected instance."""

    instance: InstanceWorkspace

    @property
    def path(self) -> Path:
        return self.instance.root / "lifecycle.jsonl"

    def list(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        records = list(self._records())
        if limit is not None:
            if limit < 0:
                raise ValueError("limit must not be negative")
            records = records[-limit:] if limit else []
        return records

    def export(self, destination: str | Path) -> Path:
        output = Path(destination)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as stream:
            for record in self._records():
                stream.write(
                    json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                )
        return output

    def _records(self) -> Iterator[dict[str, Any]]:
        with self.path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(
                        f"launch instance timeline record {line_number} must be an object"
                    )
                yield value


__all__ = ["LaunchInstanceTimelineApplication"]
