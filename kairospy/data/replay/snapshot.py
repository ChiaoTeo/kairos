from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from kairospy.infrastructure.storage.data_lake import write_json


@dataclass(frozen=True, slots=True)
class DataInputSnapshot:
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


def write_data_snapshot(path: str | Path, workspace: str, inputs: Iterable[DataInputSnapshot], *,
                        code_version: str, environment_hash: str | None = None) -> Path:
    values = tuple(inputs)
    if not workspace.strip() or not code_version.strip():
        raise ValueError("data snapshot requires workspace and code_version")
    if not values:
        raise ValueError("data snapshot requires at least one frozen input")
    target = Path(path)
    write_json(target, {
        "snapshot_schema_version": 1, "workspace": workspace,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": [asdict(item) for item in values],
        "code": {"version": code_version}, "environment": {"lock_hash": environment_hash},
    })
    return target
