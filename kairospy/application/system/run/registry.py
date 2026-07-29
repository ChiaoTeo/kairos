from __future__ import annotations

from dataclasses import dataclass, field
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
    state: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "directory": str(self.directory),
            "phase": self.phase,
            "status": self.status,
            "desired_state": self.desired_state,
            "heartbeat_at": None if self.heartbeat_at is None else self.heartbeat_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "heartbeat_age_seconds": self.heartbeat_age_seconds,
            "stale": self.stale,
            "log_file": str(self.directory / "run.log") if (self.directory / "run.log").exists() else None,
            "context": {
                **dict(self.state.get("context", {}) if isinstance(self.state.get("context"), Mapping) else {}),
                "strategy": str(self.summary.get("strategy_id") or ""),
            },
            "result": dict(self.summary),
        }

    @property
    def phase(self) -> str:
        return str(self.state.get("phase") or self.summary.get("phase") or "stopped")

    @property
    def status(self) -> str:
        return "stale" if self.stale else str(self.state.get("status") or self.summary.get("status") or self.phase)

    @property
    def desired_state(self) -> str:
        command = _read_json(self.directory / "command.json")
        return str(command.get("desired_state") or self.state.get("desired_state") or "stopped")

    @property
    def heartbeat_at(self) -> datetime | None:
        return _parse_time(self.state.get("heartbeat_at"))

    @property
    def heartbeat_age_seconds(self) -> float | None:
        heartbeat_at = self.heartbeat_at
        if heartbeat_at is None:
            return None
        return (datetime.now(timezone.utc) - heartbeat_at).total_seconds()

    @property
    def stale(self) -> bool:
        age = self.heartbeat_age_seconds
        return self.phase in {"starting", "running", "stopping"} and (age is None or age > 5.0)


class RunRegistry:
    def __init__(self, root: str | Path = ".kairos/runs") -> None:
        self.root = Path(root).expanduser()

    def list(self, *, mode: str | None = None, run_id: str | None = None) -> tuple[RunRecord, ...]:
        if not self.root.exists():
            return ()
        records: list[RunRecord] = []
        for directory in self._run_directories():
            record = self._record(directory)
            if mode is not None and record.mode != mode:
                continue
            if run_id is not None and record.run_id != run_id:
                continue
            records.append(record)
        return tuple(sorted(records, key=lambda item: (item.mode, item.run_id, str(item.directory))))

    def request_stop(self, *, mode: str, run_id: str, reason: str, actor: str = "cli") -> Path:
        directory = self._current_directory(mode=mode, run_id=run_id)
        if directory is None:
            records = self.list(mode=mode, run_id=run_id)
            directory = self.root / mode / run_id if not records else records[-1].directory
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "command.json"
        path.write_text(
            json.dumps(
                {
                    "desired_state": "stopped",
                    "reason": reason,
                    "actor": actor,
                    "requested_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def _run_directories(self) -> tuple[Path, ...]:
        paths = {path.parent for path in self.root.rglob("summary.json")}
        paths.update(path.parent for path in self.root.rglob("state.json"))
        return tuple(sorted(path for path in paths if not _is_mirrored_group_directory(path)))

    def _current_directory(self, *, mode: str, run_id: str) -> Path | None:
        current = _read_json(self.root / mode / run_id / "current.json")
        directory = current.get("directory")
        if isinstance(directory, str) and directory.strip():
            path = Path(directory)
            return path if path.is_absolute() else (self.root / mode / run_id / path)
        instance_id = current.get("run_instance_id")
        if isinstance(instance_id, str) and instance_id.strip():
            return self.root / mode / run_id / "runs" / instance_id
        return None

    def _record(self, directory: Path) -> RunRecord:
        summary_path = directory / "summary.json"
        summary = _read_summary(summary_path)
        state = _read_json(directory / "state.json")
        stat_path = summary_path if summary_path.exists() else directory / "state.json"
        stat = stat_path.stat()
        return RunRecord(
            run_id=str(state.get("run_id") or summary.get("run_id") or directory.name),
            mode=str(state.get("mode") or summary.get("mode") or directory.parent.name),
            directory=directory,
            summary_path=summary_path,
            updated_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
            summary=summary,
            state=state,
        )


def _read_summary(path: Path) -> Mapping[str, object]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"run summary must be a JSON object: {path}")
    return value


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return datetime.fromisoformat(value)


def _is_mirrored_group_directory(path: Path) -> bool:
    if not (path / "current.json").exists():
        return False
    state = _read_json(path / "state.json")
    return "mirrored_from" in state or (path / "runs").exists()


def list_run_daemons(
    *,
    mode: str | None = None,
    root: str | Path | None = None,
    stale_after_seconds: float = 5.0,
) -> tuple[RunRecord, ...]:
    _ = stale_after_seconds
    return RunRegistry(Path(".kairos/runs") if root is None else root).list(mode=mode)


__all__ = ["RunRecord", "RunRegistry", "list_run_daemons"]
