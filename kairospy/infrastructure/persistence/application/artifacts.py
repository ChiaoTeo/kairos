from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from kairospy.infrastructure.persistence.services.artifacts.run_sqlite import RunSqliteStore


class SqliteProjectionReader:
    """SQLite implementation of the launch research read capability."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._store = RunSqliteStore(self._root / "run.sqlite")

    @property
    def root(self) -> Path:
        return self._root

    def exists(self, name: str) -> bool:
        return self._store.exists(name)

    def read_json(self, name: str) -> dict[str, object]:
        if name in {"state.json", "live_state.json"}:
            return _read_projection_json(self._root / name)
        if name.startswith("account/") and name.endswith("current.json"):
            return self._store.read_current("account")
        return self._store.read_json(name)

    def read_jsonl(self, name: str) -> list[dict[str, object]]:
        return self._store.read_records(name.removesuffix(".jsonl").split("/")[-1])

    def read_records(self, stream: str) -> list[dict[str, object]]:
        """Read a canonical run stream for composition-level projections."""
        return self._store.read_records(stream)


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
        raise ValueError(f"no run-capable launch instances found{hint}")
    return max(candidates, key=_projection_mtime)


def list_projection_instances(root: str | Path, *, mode: str | None = None, launch_id: str | None = None) -> list[dict[str, object]]:
    base = Path(root).expanduser().resolve()
    if not base.exists():
        return []
    rows: list[dict[str, object]] = []
    for path in sorted(_projection_candidates(base, mode=mode, launch_id=launch_id, require_existing=False), key=_projection_mtime, reverse=True):
        if not (path / "run.sqlite").exists():
            continue
        reader = SqliteProjectionReader(path)
        summary = reader.read_json("summary.json")
        state = _read_projection_json(path / "state.json")
        rows.append(
            {
                "mode": _first_string(state.get("mode"), summary.get("mode"), _projection_mode(path)),
                "launch_id": _first_string(state.get("launch_id"), summary.get("launch_id"), _projection_launch_id(path)),
                "launch_instance_id": _first_string(state.get("launch_instance_id"), path.name),
                "strategy_id": _first_string(summary.get("strategy_id"), _projection_context_value(state, "strategy")),
                "updated_at": _projection_mtime(path),
                "directory": str(path),
                "record_count": sum(len(reader.read_records(stream)) for stream in ("timeline", "equity", "fills", "trades", "intent_states")),
                "equity_count": len(reader.read_records("equity")),
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
    return (path / "run.sqlite").exists()


def _projection_mtime(path: Path) -> float:
    existing = [item for item in (path / "run.sqlite", path / "state.json", path) if item.exists()]
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
    "SqliteProjectionReader",
    "find_projection_instance",
    "list_projection_instances",
]
