from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from kairospy.infrastructure.persistence.services.artifacts.launch_store import (
    DataNamespace,
    JsonResource,
    JsonlResource,
    LaunchInstanceStore,
    jsonable,
)


class JsonProjectionReader:
    """Filesystem implementation of the Biz projection read capability."""

    def __init__(self, root: str | Path) -> None:
        self._store = LaunchInstanceStore(root)

    @property
    def root(self) -> Path:
        return self._store.directory

    def exists(self, name: str) -> bool:
        return self._store.path_for(name).exists()

    def read_json(self, name: str) -> dict[str, object]:
        return self._store.json(name.removesuffix(".json")).read()

    def read_jsonl(self, name: str) -> list[dict[str, object]]:
        if not self._store.path_for(name).exists():
            return []
        return self._store.jsonl(name.removesuffix(".jsonl")).read()


def find_projection_instance(root: str | Path, *, mode: str | None = None, launch_id: str | None = None) -> Path:
    base = Path(root).expanduser().resolve()
    candidates = _projection_candidates(base, mode=mode, launch_id=launch_id, require_existing=True)
    candidates = [path for path in candidates if _has_projection_file(path)]
    if not candidates:
        hint = f" under {base}"
        if mode is not None:
            hint += f" mode={mode}"
        if launch_id is not None:
            hint += f" launch_id={launch_id}"
        raise ValueError(f"no timeline-capable launch instances found{hint}")
    return max(candidates, key=_projection_mtime)


def list_projection_instances(root: str | Path, *, mode: str | None = None, launch_id: str | None = None) -> list[dict[str, object]]:
    base = Path(root).expanduser().resolve()
    if not base.exists():
        return []
    rows: list[dict[str, object]] = []
    for path in sorted(_projection_candidates(base, mode=mode, launch_id=launch_id, require_existing=False), key=_projection_mtime, reverse=True):
        summary = _read_projection_json(path / "summary.json")
        state = _read_projection_json(path / "state.json")
        rows.append(
            {
                "mode": _first_string(state.get("mode"), summary.get("mode"), _projection_mode(path)),
                "launch_id": _first_string(state.get("launch_id"), summary.get("launch_id"), _projection_launch_id(path)),
                "launch_instance_id": _first_string(state.get("launch_instance_id"), path.name),
                "strategy_id": _first_string(summary.get("strategy_id"), _projection_context_value(state, "strategy")),
                "updated_at": _projection_mtime(path),
                "directory": str(path),
                "timeline_count": _jsonl_count(path / "timeline.jsonl"),
                "equity_count": _jsonl_count(path / "equity.jsonl"),
            }
        )
    return rows


def _projection_candidates(base: Path, *, mode: str | None, launch_id: str | None, require_existing: bool) -> list[Path]:
    if mode is None and launch_id is None:
        paths = base.glob("*/*/instances/*")
    elif mode is not None and launch_id is not None:
        paths = (base / mode / launch_id / "instances").glob("*")
    elif mode is not None:
        paths = (base / mode).glob("*/instances/*")
    else:
        paths = base.glob(f"*/{launch_id}/instances/*")
    return [path for path in paths if path.is_dir() and (not require_existing or path.exists())]


def _has_projection_file(path: Path) -> bool:
    return any((path / name).exists() for name in ("timeline.jsonl", "summary.json", "state.json"))


def _projection_mtime(path: Path) -> float:
    existing = [item for item in (path / "summary.json", path / "state.json", path) if item.exists()]
    return max(item.stat().st_mtime for item in existing)


def _read_projection_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _jsonl_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _projection_mode(path: Path) -> str | None:
    parts = path.parts
    return parts[parts.index("launches") + 1] if "launches" in parts and len(parts) > parts.index("launches") + 1 else None


def _projection_launch_id(path: Path) -> str | None:
    parts = path.parts
    return parts[parts.index("launches") + 2] if "launches" in parts and len(parts) > parts.index("launches") + 2 else None


def _projection_context_value(state: Mapping[str, object], key: str) -> object:
    context = state.get("context")
    return context.get(key) if isinstance(context, Mapping) else None


def _first_string(*values: object) -> str | None:
    return next((value for value in values if isinstance(value, str) and value.strip()), None)


__all__ = [
    "DataNamespace",
    "JsonProjectionReader",
    "JsonResource",
    "JsonlResource",
    "LaunchInstanceStore",
    "find_projection_instance",
    "jsonable",
    "list_projection_instances",
]
