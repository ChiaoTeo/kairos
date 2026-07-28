from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    mode: str
    directory: Path
    summary_path: Path
    updated_at: datetime
    summary: Mapping[str, object]


class RunRegistry:
    def __init__(self, root: str | Path = ".kairos/runs") -> None:
        self.root = Path(root).expanduser()

    def list(self, *, mode: str | None = None, run_id: str | None = None) -> tuple[RunRecord, ...]:
        if not self.root.exists():
            return ()
        records: list[RunRecord] = []
        for summary_path in sorted(self.root.rglob("summary.json")):
            record = self._record(summary_path)
            if mode is not None and record.mode != mode:
                continue
            if run_id is not None and record.run_id != run_id:
                continue
            records.append(record)
        return tuple(sorted(records, key=lambda item: (item.mode, item.run_id, str(item.directory))))

    def _record(self, summary_path: Path) -> RunRecord:
        summary = _read_summary(summary_path)
        stat = summary_path.stat()
        return RunRecord(
            run_id=str(summary.get("run_id") or summary_path.parent.name),
            mode=str(summary.get("mode") or summary_path.parent.parent.name),
            directory=summary_path.parent,
            summary_path=summary_path,
            updated_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
            summary=summary,
        )


def _read_summary(path: Path) -> Mapping[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"run summary must be a JSON object: {path}")
    return value


__all__ = ["RunRecord", "RunRegistry"]
