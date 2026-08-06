from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import time
from pathlib import Path
from typing import Mapping

from kairospy.application.support.launch.application.control import LaunchControl
from kairospy.application.support.launch.application.control.facade import LaunchApplication, record_payload
from kairospy.application.support.launch.domain.modes import RuntimeMode
from kairospy.application.support.launch.application.configuration import SYSTEM_LAUNCH_ID


@dataclass(frozen=True, slots=True)
class AttachTarget:
    mode: RuntimeMode
    launch_id: str
    root: Path
    directory: Path
    state_file: Path
    log_file: Path | None


@dataclass(frozen=True, slots=True)
class LogChunk:
    text: str
    position: int


class LaunchAttachSession:
    def __init__(self, target: AttachTarget, *, launches: LaunchApplication | None = None) -> None:
        self.target = target
        self._launches = launches or LaunchApplication()
        self._log_file_seen = target.log_file

    @classmethod
    def resolve(
        cls,
        *,
        target: str | None = None,
        mode: RuntimeMode | None = None,
        launch_id: str | None = None,
        root: Path | None = None,
        launches: LaunchApplication | None = None,
    ) -> "LaunchAttachSession":
        facade = launches or LaunchApplication()
        return cls(resolve_attach_target(target=target, mode=mode, launch_id=launch_id, root=root, launches=facade), launches=facade)

    @classmethod
    def system(
        cls,
        *,
        root: Path | None = None,
        launch_id: str = SYSTEM_LAUNCH_ID,
        launches: LaunchApplication | None = None,
    ) -> "LaunchAttachSession":
        return cls.resolve(target=None, mode=RuntimeMode.SYSTEM, launch_id=launch_id, root=root, launches=launches)

    def status(self) -> Mapping[str, object]:
        self.refresh_target()
        records = tuple(record_payload(record) for record in self._records())
        state = read_json_mapping(self.target.state_file)
        heartbeat_at = state.get("heartbeat_at")
        heartbeat_age = heartbeat_age_seconds(str(heartbeat_at)) if isinstance(heartbeat_at, str) else None
        return {
            "mode": self.target.mode.value,
            "launch_id": self.target.launch_id,
            "root": str(self.target.root),
            "directory": str(self.target.directory),
            "state_file": str(self.target.state_file),
            "log_file": None if self.target.log_file is None else str(self.target.log_file),
            "phase": state.get("phase") or state.get("status") or "unknown",
            "heartbeat_at": heartbeat_at,
            "heartbeat_age_seconds": heartbeat_age,
            "records": list(records),
            "record_count": len(records),
        }

    def inspect(self) -> Mapping[str, object]:
        self.refresh_target()
        if self.target.mode is RuntimeMode.SYSTEM:
            return self._launches.system_inspect(root=self.target.root, launch_id=self.target.launch_id)
        return {
            "mode": self.target.mode.value,
            "launch_id": self.target.launch_id,
            "root": str(self.target.root),
            "current_record": self._current_record(),
            "records": [record_payload(record) for record in self._records()],
        }

    def read_log_since(self, position: int) -> LogChunk:
        self.refresh_target()
        if self.target.log_file != self._log_file_seen:
            position = 0
            self._log_file_seen = self.target.log_file
        if self.target.log_file is None:
            return LogChunk("", position)
        text, next_position = read_file_chunk(self.target.log_file, position)
        return LogChunk(text, next_position)

    def read_state(self) -> Mapping[str, object]:
        self.refresh_target()
        return read_json_mapping(self.target.state_file)

    def refresh_target(self) -> bool:
        refreshed = resolve_attach_target(
            target=None,
            mode=self.target.mode,
            launch_id=self.target.launch_id,
            root=self.target.root,
            launches=self._launches,
        )
        if refreshed == self.target:
            return False
        self.target = refreshed
        return True

    def submit_command(
        self,
        kind: str,
        payload: Mapping[str, object] | None = None,
        *,
        wait: bool = True,
        timeout_seconds: float = 5.0,
    ) -> Mapping[str, object]:
        result = LaunchControl(self.target.root).submit_command(
            mode=self.target.mode,
            launch_id=self.target.launch_id,
            kind=kind,
            payload=dict(payload or {}),
            actor="attach",
        )
        if wait:
            result = result | {"response": wait_for_command_response(Path(str(result["response_file"])), timeout_seconds=timeout_seconds)}
        return result

    def stop(self, *, reason: str = "requested from attach") -> Mapping[str, object]:
        path = LaunchControl(self.target.root).request_stop(
            mode=self.target.mode,
            launch_id=self.target.launch_id,
            reason=reason,
            actor="attach",
        )
        return {
            "command_file": str(path),
            "mode": self.target.mode.value,
            "launch_id": self.target.launch_id,
            "desired_state": "stopped",
        }

    def trade_status(self, account: str | None = None) -> Mapping[str, object]:
        return self.submit_command("account.trade-status", {"account": account} if account else {})

    def trade_acquire(self, account: str) -> Mapping[str, object]:
        return self.submit_command("account.trade-acquire", {"account": account})

    def trade_release(self, account: str) -> Mapping[str, object]:
        return self.submit_command("account.trade-release", {"account": account})

    def _records(self) -> tuple[object, ...]:
        return self._launches.records(
            target=None,
            mode=self.target.mode,
            launch_id=self.target.launch_id,
            root=self.target.root,
        )

    def _current_record(self) -> Mapping[str, object]:
        for record in self._records():
            payload = record_payload(record)
            if Path(str(payload.get("directory") or "")) == self.target.directory:
                return payload
        return {}


def resolve_attach_target(
    *,
    target: str | None,
    mode: RuntimeMode | None,
    launch_id: str | None,
    root: Path | None,
    launches: LaunchApplication | None = None,
) -> AttachTarget:
    facade = launches or LaunchApplication()
    resolved_mode, resolved_launch_id = facade.launch_identity(target, mode=mode, launch_id=launch_id, root=root, require_mode=False)
    launch_root = facade.launch_root(root)
    if resolved_mode is None and resolved_launch_id is not None:
        resolved_mode = _mode_for_launch_id(launch_root, resolved_launch_id)
    if resolved_mode is None and target is None and launch_id is None:
        resolved_mode = RuntimeMode.SYSTEM
    if resolved_mode is None:
        raise ValueError("attach requires TARGET or --mode")
    if resolved_launch_id is None:
        if resolved_mode is RuntimeMode.SYSTEM:
            resolved_launch_id = SYSTEM_LAUNCH_ID
        else:
            raise ValueError("attach requires a launch id when target is not a registered launch/config")
    records = tuple(
        record_payload(record)
        for record in LaunchControl(launch_root).list(mode=resolved_mode, launch_id=resolved_launch_id)
    )
    directory = _current_directory(launch_root, resolved_mode, resolved_launch_id)
    if directory is None:
        directory = _select_record_directory(records)
    if directory is None:
        raise ValueError(f"launch record was not found: {resolved_mode.value}:{resolved_launch_id}")
    return AttachTarget(
        mode=resolved_mode,
        launch_id=resolved_launch_id,
        root=launch_root,
        directory=directory,
        state_file=directory / "state.json",
        log_file=_first_log_file((directory / "daemon.log", directory / "launch.log")),
    )


def read_file_chunk(path: Path, position: int) -> tuple[str, int]:
    if not path.exists():
        return "", position
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(position)
        chunk = handle.read()
        return chunk, handle.tell()


def read_json_mapping(path: Path) -> Mapping[str, object]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def heartbeat_age_seconds(heartbeat_at: str) -> float | None:
    try:
        parsed = datetime.fromisoformat(heartbeat_at)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def wait_for_command_response(path: Path, *, timeout_seconds: float) -> Mapping[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, Mapping) else {}
        time.sleep(0.05)
    raise ValueError(f"runtime command response timed out: {path}")


def _current_directory(root: Path, mode: RuntimeMode, launch_id: str) -> Path | None:
    current = read_json_mapping(root / mode.value / launch_id / "current.json")
    directory = current.get("directory")
    if isinstance(directory, str) and directory.strip():
        path = Path(directory)
        return path if path.is_absolute() else root / mode.value / launch_id / path
    instance_id = current.get("launch_instance_id")
    if isinstance(instance_id, str) and instance_id.strip():
        return root / mode.value / launch_id / "instances" / instance_id
    return None


def _select_record_directory(records: tuple[Mapping[str, object], ...]) -> Path | None:
    active = [
        record
        for record in records
        if record.get("phase") in {"starting", "running", "stopping"} and not bool(record.get("stale"))
    ]
    candidates = active or list(records)
    if not candidates:
        return None
    directory = candidates[-1].get("directory")
    return Path(str(directory)) if directory else None


def _mode_for_launch_id(root: Path, launch_id: str) -> RuntimeMode | None:
    modes = []
    for value in RuntimeMode:
        if value is RuntimeMode.SYSTEM:
            continue
        if (root / value.value / launch_id).exists():
            modes.append(value)
    if len(modes) == 1:
        return modes[0]
    return None


def _first_log_file(candidates: tuple[Path, ...]) -> Path | None:
    existing = [candidate for candidate in candidates if candidate.exists()]
    for candidate in existing:
        if candidate.stat().st_size > 0:
            return candidate
    return existing[0] if existing else None


__all__ = [
    "AttachTarget",
    "LaunchAttachSession",
    "LogChunk",
    "heartbeat_age_seconds",
    "read_file_chunk",
    "read_json_mapping",
    "resolve_attach_target",
    "wait_for_command_response",
]
