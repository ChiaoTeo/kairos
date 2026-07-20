from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from kairospy.storage.data_lake import write_json


@dataclass(frozen=True, slots=True)
class StudyInputSnapshot:
    logical_key: str
    release_id: str
    content_hash: str
    schema_version: str
    transform_id: str
    transform_version: str
    provider: str | None
    venue: str | None
    quality_level: str
    source_policy_version: str
    view: str
    start: str | None
    end: str | None
    boundary: str
    fields: tuple[str, ...] | None
    instruments: tuple[str, ...]
    event_types: tuple[str, ...]


def write_study_snapshot(path: str | Path, study_id: str, inputs: Iterable[StudyInputSnapshot], *,
                         code_version: str, environment_hash: str | None = None) -> Path:
    values = tuple(inputs)
    if not study_id.strip() or not code_version.strip():
        raise ValueError("study snapshot requires study_id and code_version")
    if not values:
        raise ValueError("study snapshot requires at least one frozen input")
    target = Path(path)
    write_json(target, {
        "snapshot_schema_version": 1, "study_id": study_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": [asdict(item) for item in values],
        "code": {"version": code_version}, "environment": {"lock_hash": environment_hash},
    })
    return target
