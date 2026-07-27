from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import errno
import fcntl
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import threading
import time
from typing import Mapping, Protocol
from uuid import uuid4

from kairospy import __version__
from kairospy.config import load_config
from kairospy.runtime.line import RuntimeMode


class RunDaemonPhase(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RunProcessIdentity:
    run_id: str
    mode: RuntimeMode
    process_id: str
    pid: int
    host: str
    version: str
    started_at: datetime

    @classmethod
    def create(cls, run_id: str, mode: RuntimeMode | str = RuntimeMode.LIVE) -> "RunProcessIdentity":
        started_at = _now()
        host = platform.node() or "localhost"
        pid = os.getpid()
        process_id = f"{host}:{pid}:{started_at.isoformat()}:{uuid4()}"
        return cls(run_id, _runtime_mode(mode), process_id, pid, host, __version__, started_at)

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "mode": self.mode.value,
            "process_id": self.process_id,
            "pid": self.pid,
            "host": self.host,
            "version": self.version,
            "started_at": self.started_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class RunDaemonStatus:
    run_id: str
    mode: RuntimeMode
    phase: RunDaemonPhase
    reason: str
    desired_state: str
    identity: dict[str, object] | None
    heartbeat_at: datetime | None
    updated_at: datetime
    stale: bool = False
    heartbeat_age_seconds: float | None = None
    log_file: str | None = None
    context: dict[str, object] | None = None
    metrics: dict[str, object] | None = None
    result: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "mode": self.mode.value,
            "phase": self.phase.value,
            "status": "stale" if self.stale else self.phase.value,
            "reason": self.reason,
            "desired_state": self.desired_state,
            "identity": self.identity,
            "heartbeat_at": self.heartbeat_at.isoformat() if self.heartbeat_at is not None else None,
            "updated_at": self.updated_at.isoformat(),
            "stale": self.stale,
            "heartbeat_age_seconds": self.heartbeat_age_seconds,
            "log_file": self.log_file,
            "context": dict(self.context or {}),
            "metrics": dict(self.metrics or {}),
            "result": dict(self.result or {}),
        }


class RunDaemonTarget(Protocol):
    def run(self, context: "RunExecutionContext") -> Mapping[str, object] | None:
        ...


class RunExecutionContext:
    def __init__(self, control: "RunDaemonControlPlane", identity: RunProcessIdentity) -> None:
        self.control = control
        self.identity = identity
        self._stop_requested = False
        self._stop_reason = ""

    @property
    def run_id(self) -> str:
        return self.control.run_id

    @property
    def mode(self) -> RuntimeMode:
        return self.control.mode

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    @property
    def stop_reason(self) -> str:
        return self._stop_reason

    def request_stop(self, reason: str) -> None:
        self._stop_requested = True
        self._stop_reason = reason or "operator stop requested"

    def poll_control(self) -> bool:
        command = self.control._read_json(self.control.command_path)
        if command.get("type") == "stop":
            self.request_stop(str(command.get("reason", "operator stop requested")))
        return self.stop_requested

    def heartbeat(
        self,
        phase: RunDaemonPhase | str = RunDaemonPhase.RUNNING,
        reason: str = "running",
        *,
        metrics: Mapping[str, object] | None = None,
        result: Mapping[str, object] | None = None,
        desired_state: str = "running",
    ) -> RunDaemonStatus:
        return self.control._persist(
            RunDaemonPhase(phase),
            reason,
            self.identity,
            desired_state=desired_state,
            metrics=dict(metrics or {}),
            result=dict(result or {}),
        )


class RunFileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def acquire(self, identity: RunProcessIdentity) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            owner = self.owner()
            handle.close()
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise RuntimeError(
                    f"{identity.mode.value} run {identity.run_id!r} is already running ({owner})"
                ) from error
            raise
        self._handle = handle
        self.write_owner(identity)

    def write_owner(self, identity: RunProcessIdentity) -> None:
        if self._handle is None:
            return
        payload = {**identity.to_dict(), "heartbeat_at": _now().isoformat()}
        self._handle.seek(0)
        self._handle.truncate()
        json.dump(payload, self._handle, sort_keys=True)
        self._handle.write("\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            handle.seek(0)
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            pass
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def owner(self) -> str:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except (FileNotFoundError, json.JSONDecodeError):
            payload = {}
        parts = []
        for key in ("run_id", "mode", "pid", "host", "heartbeat_at"):
            if payload.get(key) is not None:
                parts.append(f"{key}={payload[key]}")
        return ", ".join(parts) or "unknown owner"


class RunDaemonControlPlane:
    def __init__(
        self,
        run_id: str,
        *,
        mode: RuntimeMode | str = RuntimeMode.LIVE,
        root: str | Path | None = None,
    ) -> None:
        self.run_id = _required_text(run_id, "run_id")
        self.mode = _runtime_mode(mode)
        base = Path(root).expanduser() if root is not None else load_config().resolve_path(
            f".kairos/runtime/{self.mode.value}"
        )
        self.directory = base / _path_segment(self.run_id)
        self.state_path = self.directory / "state.json"
        self.context_path = self.directory / "context.json"
        self.events_path = self.directory / "events.jsonl"
        self.command_path = self.directory / "command.json"
        self.lock_path = self.directory / "run.lock"
        self.log_path = self.directory / "daemon.log"

    def status(self, *, stale_after_seconds: float = 5.0) -> RunDaemonStatus:
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        state = self._read_json(self.state_path)
        mode = _runtime_mode(state.get("mode", self.mode.value))
        phase = RunDaemonPhase(str(state.get("phase", RunDaemonPhase.CREATED.value)))
        heartbeat_at = _parse_time(state.get("heartbeat_at"))
        now = _now()
        age = (now - heartbeat_at).total_seconds() if heartbeat_at is not None else None
        stale = (
            phase in {RunDaemonPhase.STARTING, RunDaemonPhase.RUNNING, RunDaemonPhase.STOPPING}
            and (age is None or age > stale_after_seconds)
        )
        return RunDaemonStatus(
            self.run_id,
            mode,
            phase,
            str(state.get("reason", "created")),
            str(state.get("desired_state", "running" if phase is RunDaemonPhase.RUNNING else phase.value)),
            state.get("identity") if isinstance(state.get("identity"), dict) else None,
            heartbeat_at,
            _parse_time(state.get("updated_at")) or now,
            stale=stale,
            heartbeat_age_seconds=age,
            log_file=str(self.log_path),
            context=state.get("context") if isinstance(state.get("context"), dict) else self._read_json(self.context_path),
            metrics=state.get("metrics") if isinstance(state.get("metrics"), dict) else {},
            result=state.get("result") if isinstance(state.get("result"), dict) else {},
        )

    def update_context(self, values: Mapping[str, object]) -> dict[str, object]:
        self.directory.mkdir(parents=True, exist_ok=True)
        existing = self._read_json(self.context_path)
        context = {**existing, **dict(values)}
        context.setdefault("run_id", self.run_id)
        context.setdefault("mode", self.mode.value)
        context.setdefault("state_file", str(self.state_path))
        context.setdefault("log_file", str(self.log_path))
        context.setdefault("events_file", str(self.events_path))
        self.context_path.write_text(json.dumps(context, indent=2, sort_keys=True), encoding="utf-8")
        state = self._read_json(self.state_path)
        if state:
            state["context"] = context
            state["updated_at"] = _now().isoformat()
            self._write_state(state)
        self.record_event("context", context=context)
        return context

    def record_event(self, event_type: str, **payload: object) -> dict[str, object]:
        self.directory.mkdir(parents=True, exist_ok=True)
        event = {
            "time": _now().isoformat(),
            "type": _required_text(event_type, "event_type"),
            "run_id": self.run_id,
            "mode": self.mode.value,
            **payload,
        }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, default=str) + "\n")
        return event

    def request_stop(self, *, reason: str, actor: str = "cli", force: bool = False) -> dict[str, object]:
        self.directory.mkdir(parents=True, exist_ok=True)
        command = {
            "type": "stop",
            "run_id": self.run_id,
            "mode": self.mode.value,
            "reason": _required_text(reason, "reason"),
            "actor": _required_text(actor, "actor"),
            "force": bool(force),
            "requested_at": _now().isoformat(),
        }
        self.command_path.write_text(json.dumps(command, indent=2, sort_keys=True), encoding="utf-8")
        self.record_event("command", command=command)
        state = self._read_json(self.state_path)
        if state:
            state.update({
                "desired_state": "stopped",
                "stop_requested": True,
                "reason": command["reason"],
                "updated_at": _now().isoformat(),
            })
            self._write_state(state)
        return command

    def start_background(
        self,
        *,
        poll_seconds: float = 1.0,
        stale_after_seconds: float = 5.0,
        log_file: str | Path | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> RunDaemonStatus:
        self.directory.mkdir(parents=True, exist_ok=True)
        log_path = Path(log_file).expanduser() if log_file is not None else self.log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        args = [
            sys.executable,
            "-m",
            "kairospy",
            "run",
            "daemon",
            "start",
            "--mode",
            self.mode.value,
            "--run-id",
            self.run_id,
            "--foreground",
            "--poll-seconds",
            str(poll_seconds),
            *extra_args,
        ]
        with log_path.open("ab") as output:
            subprocess.Popen(
                args,
                stdout=output,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        self.update_context({
            "launch": "background",
            "args": args,
            "log_file": str(log_path),
        })
        self.record_event("start_requested", launch="background", args=args, log_file=str(log_path))
        deadline = time.monotonic() + min(stale_after_seconds, 2.0)
        status = self.status(stale_after_seconds=stale_after_seconds)
        while time.monotonic() < deadline:
            status = self.status(stale_after_seconds=stale_after_seconds)
            if status.phase in {RunDaemonPhase.STARTING, RunDaemonPhase.RUNNING, RunDaemonPhase.FAILED}:
                break
            time.sleep(0.05)
        return status

    def run_foreground(
        self,
        *,
        poll_seconds: float = 1.0,
        duration_seconds: float | None = None,
        target: RunDaemonTarget | None = None,
    ) -> RunDaemonStatus:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        if duration_seconds is not None and duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        identity = RunProcessIdentity.create(self.run_id, self.mode)
        lock = RunFileLock(self.lock_path)
        context = RunExecutionContext(self, identity)
        stop_reason = "manual stop"
        stopping = False

        def request_signal_stop(signum: int, _frame: object) -> None:
            nonlocal stop_reason, stopping
            stop_reason = f"signal {signum}"
            stopping = True

        install_signal_handlers = threading.current_thread() is threading.main_thread()
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        previous_sigint = signal.getsignal(signal.SIGINT)
        if install_signal_handlers:
            previous_sigterm = signal.signal(signal.SIGTERM, request_signal_stop)
            previous_sigint = signal.signal(signal.SIGINT, request_signal_stop)
        try:
            lock.acquire(identity)
            self._persist(RunDaemonPhase.STARTING, "started", identity, desired_state="running")
            self.update_context({
                "launch": "foreground",
                "pid": identity.pid,
                "host": identity.host,
                "process_id": identity.process_id,
                "started_at": identity.started_at.isoformat(),
            })
            context.heartbeat(RunDaemonPhase.RUNNING, "running")
            if target is not None:
                controller = _ControlPoller(context, lock, poll_seconds)
                controller.start()
                try:
                    target_result = target.run(context)
                finally:
                    controller.stop()
                stop_reason = context.stop_reason or "target completed"
                return self._stop_after_target(identity, lock, stop_reason, target_result)
            started = time.monotonic()
            while not stopping:
                if context.poll_control():
                    stop_reason = context.stop_reason
                    stopping = True
                    break
                if duration_seconds is not None and time.monotonic() - started >= duration_seconds:
                    stop_reason = "duration elapsed"
                    break
                context.heartbeat(RunDaemonPhase.RUNNING, "running")
                lock.write_owner(identity)
                time.sleep(poll_seconds)
            self._persist(RunDaemonPhase.STOPPING, stop_reason, identity, desired_state="stopped")
            if self.command_path.exists():
                self.command_path.unlink()
            return self._persist(RunDaemonPhase.STOPPED, stop_reason, identity, desired_state="stopped")
        except Exception as error:
            self._persist(RunDaemonPhase.FAILED, f"{type(error).__name__}: {error}", identity, desired_state="stopped")
            raise
        finally:
            lock.release()
            if install_signal_handlers:
                signal.signal(signal.SIGTERM, previous_sigterm)
                signal.signal(signal.SIGINT, previous_sigint)

    def _stop_after_target(
        self,
        identity: RunProcessIdentity,
        lock: RunFileLock,
        stop_reason: str,
        target_result: Mapping[str, object] | None,
    ) -> RunDaemonStatus:
        result = dict(target_result or {})
        metrics = self._read_json(self.state_path).get("metrics")
        metrics = metrics if isinstance(metrics, dict) else {}
        self._persist(
            RunDaemonPhase.STOPPING,
            stop_reason,
            identity,
            desired_state="stopped",
            metrics=metrics,
            result=result,
        )
        if self.command_path.exists():
            self.command_path.unlink()
        lock.write_owner(identity)
        return self._persist(
            RunDaemonPhase.STOPPED,
            stop_reason,
            identity,
            desired_state="stopped",
            metrics=metrics,
            result=result,
        )

    def _persist(
        self,
        phase: RunDaemonPhase,
        reason: str,
        identity: RunProcessIdentity,
        *,
        desired_state: str,
        metrics: Mapping[str, object] | None = None,
        result: Mapping[str, object] | None = None,
    ) -> RunDaemonStatus:
        now = _now()
        payload = {
            "run_id": self.run_id,
            "mode": self.mode.value,
            "phase": phase.value,
            "reason": reason,
            "desired_state": desired_state,
            "identity": identity.to_dict(),
            "heartbeat_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "log_file": str(self.log_path),
            "context": self._read_json(self.context_path),
            "metrics": dict(metrics or {}),
            "result": dict(result or {}),
        }
        self._write_state(payload)
        self.record_event(
            "status",
            phase=phase.value,
            reason=reason,
            desired_state=desired_state,
            metrics=dict(metrics or {}),
            result=dict(result or {}),
        )
        return self.status()

    def _write_state(self, payload: dict[str, object]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / f".{self.state_path.name}.{uuid4()}.tmp"
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.state_path)

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}


def list_run_daemons(
    *,
    mode: RuntimeMode | str | None = None,
    root: str | Path | None = None,
    stale_after_seconds: float = 5.0,
) -> tuple[RunDaemonStatus, ...]:
    modes = (_runtime_mode(mode),) if mode is not None else tuple(RuntimeMode)
    statuses: list[RunDaemonStatus] = []
    for item in modes:
        base = _runtime_base(item, root=root)
        if not base.exists():
            continue
        for directory in sorted(path for path in base.iterdir() if path.is_dir()):
            control = RunDaemonControlPlane(directory.name, mode=item, root=base)
            statuses.append(control.status(stale_after_seconds=stale_after_seconds))
    return tuple(sorted(statuses, key=lambda status: (status.mode.value, status.run_id)))


def _required_text(value: object, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _path_segment(value: str) -> str:
    return "".join(character if character.isalnum() or character in {"-", "_", "."} else "_" for character in value)


def _runtime_mode(value: object) -> RuntimeMode:
    return value if isinstance(value, RuntimeMode) else RuntimeMode(str(value))


def _runtime_base(mode: RuntimeMode, *, root: str | Path | None = None) -> Path:
    if root is not None:
        path = Path(root).expanduser()
        return path if path.name == mode.value else path / mode.value
    return load_config().resolve_path(f".kairos/runtime/{mode.value}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return datetime.fromisoformat(value)


class _ControlPoller:
    def __init__(self, context: RunExecutionContext, lock: RunFileLock, poll_seconds: float) -> None:
        self.context = context
        self.lock = lock
        self.poll_seconds = poll_seconds
        self._stopped = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"{context.mode.value}-run-control:{context.run_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        self._thread.join(timeout=max(self.poll_seconds * 2, 0.1))

    def _run(self) -> None:
        while not self._stopped.wait(self.poll_seconds):
            self.context.poll_control()
            self.lock.write_owner(self.context.identity)


__all__ = [
    "RunDaemonControlPlane",
    "RunDaemonPhase",
    "RunExecutionContext",
    "RunFileLock",
    "RunProcessIdentity",
    "RunDaemonStatus",
    "RunDaemonTarget",
]
