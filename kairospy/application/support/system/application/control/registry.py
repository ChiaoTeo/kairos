from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Mapping

from kairospy.application.support.system.application.session import SystemCommandFileQueue


@dataclass(frozen=True, slots=True)
class LaunchRecord:
    launch_id: str
    mode: str
    directory: Path
    summary_path: Path
    updated_at: datetime
    summary: Mapping[str, object]
    state: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        identity = self.identity
        pid = _optional_int(identity.get("pid"))
        pid_alive = None if pid is None else _pid_alive(pid)
        return {
            "launch_id": self.launch_id,
            "mode": self.mode,
            "launch_instance_id": self.launch_instance_id,
            "pid": pid,
            "pid_alive": pid_alive,
            "process_dead": self.process_dead,
            "identity": identity,
            "directory": str(self.directory),
            "phase": self.phase,
            "status": self.status,
            "health": self.health,
            "desired_state": self.desired_state,
            "heartbeat_at": None if self.heartbeat_at is None else self.heartbeat_at.isoformat(),
            "heartbeat_fresh": not self.stale if self.active else None,
            "updated_at": self.updated_at.isoformat(),
            "heartbeat_age_seconds": self.heartbeat_age_seconds,
            "stale": self.stale,
            "stale_reason": self.stale_reason,
            "log_file": str(self.directory / "launch.log") if (self.directory / "launch.log").exists() else None,
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
    def launch_instance_id(self) -> str | None:
        value = self.state.get("launch_instance_id") or self.state.get("process_id")
        return value if isinstance(value, str) and value.strip() else None

    @property
    def identity(self) -> Mapping[str, object]:
        value = self.state.get("identity")
        return value if isinstance(value, Mapping) else {}

    @property
    def status(self) -> str:
        return "stale" if self.stale else str(self.state.get("status") or self.summary.get("status") or self.phase)

    @property
    def active(self) -> bool:
        return self.phase in {"starting", "running", "stopping"}

    @property
    def health(self) -> str:
        if self.process_dead:
            return "dead"
        if self.active and self.stale:
            return "stale"
        if self.active:
            pid = _optional_int(self.identity.get("pid"))
            if pid is None:
                return "unknown"
            return "healthy"
        return self.status

    @property
    def process_dead(self) -> bool:
        pid = _optional_int(self.identity.get("pid"))
        return self.active and pid is not None and _pid_alive(pid) is False

    @property
    def stale_reason(self) -> str | None:
        if not self.stale:
            return None
        if self.process_dead:
            return "process_dead"
        if self.heartbeat_at is None:
            return "heartbeat_missing"
        return "heartbeat_expired"

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


class LaunchRegistry:
    def __init__(self, root: str | Path = ".kairos/launches") -> None:
        self.root = Path(root).expanduser()

    def list(self, *, mode: str | None = None, launch_id: str | None = None) -> tuple[LaunchRecord, ...]:
        if not self.root.exists():
            return ()
        records: list[LaunchRecord] = []
        for directory in self._launch_directories():
            record = self._record(directory)
            if mode is not None and record.mode != mode:
                continue
            if launch_id is not None and record.launch_id != launch_id:
                continue
            records.append(record)
        return tuple(sorted(records, key=lambda item: (item.mode, item.launch_id, str(item.directory))))

    def request_stop(self, *, mode: str, launch_id: str, reason: str, actor: str = "cli") -> Path:
        directory = self._current_directory(mode=mode, launch_id=launch_id)
        if directory is None:
            records = self.list(mode=mode, launch_id=launch_id)
            directory = self.root / mode / launch_id if not records else records[-1].directory
        directory.mkdir(parents=True, exist_ok=True)
        command = SystemCommandFileQueue(directory).submit(
            "runtime.stop",
            {"desired_state": "stopped", "reason": reason},
            actor=actor,
        )
        path = directory / "command.json"
        path.write_text(
            json.dumps(
                {
                    "command_id": command.command_id,
                    "kind": command.kind,
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

    def submit_command(
        self,
        *,
        mode: str,
        launch_id: str,
        kind: str,
        payload: Mapping[str, object] | None = None,
        actor: str = "cli",
    ) -> dict[str, object]:
        directory = self._current_directory(mode=mode, launch_id=launch_id)
        if directory is None:
            raise ValueError(_inactive_command_message(mode, launch_id))
        try:
            record = self._record(directory)
        except FileNotFoundError as error:
            raise ValueError(_inactive_command_message(mode, launch_id)) from error
        if not _record_accepts_commands(record):
            raise ValueError(_inactive_command_message(mode, launch_id))
        command = SystemCommandFileQueue(directory).submit(kind, payload, actor=actor)
        return {
            "launch_id": launch_id,
            "mode": mode,
            "directory": str(directory),
            "command_id": command.command_id,
            "kind": command.kind,
            "command_file": str(SystemCommandFileQueue(directory).command_path(command.command_id)),
            "response_file": str(SystemCommandFileQueue(directory).response_path(command.command_id)),
        }

    def _launch_directories(self) -> tuple[Path, ...]:
        paths = {path.parent for path in self.root.rglob("summary.json")}
        paths.update(path.parent for path in self.root.rglob("state.json"))
        return tuple(sorted(path for path in paths if not _is_mirrored_group_directory(path)))

    def _current_directory(self, *, mode: str, launch_id: str) -> Path | None:
        current = _read_json(self.root / mode / launch_id / "current.json")
        directory = current.get("directory")
        if isinstance(directory, str) and directory.strip():
            path = Path(directory)
            return path if path.is_absolute() else (self.root / mode / launch_id / path)
        instance_id = current.get("launch_instance_id")
        if isinstance(instance_id, str) and instance_id.strip():
            return self.root / mode / launch_id / "instances" / instance_id
        return None

    def _record(self, directory: Path) -> LaunchRecord:
        summary_path = directory / "summary.json"
        summary = _read_summary(summary_path)
        state = _read_json(directory / "state.json")
        stat_path = summary_path if summary_path.exists() else directory / "state.json"
        stat = stat_path.stat()
        return LaunchRecord(
            launch_id=str(state.get("launch_id") or summary.get("launch_id") or directory.name),
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
        raise ValueError(f"launch summary must be a JSON object: {path}")
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


def _optional_int(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_mirrored_group_directory(path: Path) -> bool:
    if not (path / "current.json").exists():
        return False
    state = _read_json(path / "state.json")
    return "mirrored_from" in state or (path / "instances").exists()


def _record_accepts_commands(record: LaunchRecord) -> bool:
    return record.phase in {"starting", "running", "stopping"} and not record.stale


def _inactive_command_message(mode: str, launch_id: str) -> str:
    if mode == "system":
        return f"system runtime is not running for {launch_id}; start it with `kairospy system up`"
    return (
        f"launch is not running for {mode}:{launch_id}; "
        "start it with `kairospy launch start TARGET` or pass --mode and --config"
    )


def list_launch_daemons(
    *,
    mode: str | None = None,
    root: str | Path | None = None,
    stale_after_seconds: float = 5.0,
) -> tuple[LaunchRecord, ...]:
    _ = stale_after_seconds
    return LaunchRegistry(Path(".kairos/launches") if root is None else root).list(mode=mode)


__all__ = ["LaunchRecord", "LaunchRegistry", "list_launch_daemons"]
