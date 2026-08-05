from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
import fcntl
import json
from pathlib import Path
import socket
import os
import signal
import subprocess
import sys
import threading
from contextlib import contextmanager
from typing import Callable, Mapping
from uuid import uuid4

from kairospy.application.support.launch.domain.modes import RuntimeMode
from kairospy.application.support.launch.application.launcher import TradingConfigurationError
from kairospy.application.support.launch.application.protocol import LaunchRequest, LaunchTarget, LaunchTargetDescriptor, LaunchTargetFactory
from kairospy.application.support.launch.application.commands import SystemCommand
from kairospy.application.support.launch.services.command_queue import SystemCommandFileQueue
from kairospy.application.support.launch.application.configuration import RESERVED_LAUNCH_IDS, SYSTEM_LAUNCH_ID
from kairospy.application.support.launch.application.commands import SystemCommandHandler


_LAUNCH_INSTANCE_ID_ENV = "KAIROS_LAUNCH_INSTANCE_ID"
_ACTIVE_PHASES = {"starting", "running", "stopping"}
_STALE_AFTER_SECONDS = 5.0
_HEARTBEAT_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class LaunchDaemonResult:
    launch_id: str
    mode: str
    directory: Path
    state_path: Path
    summary_path: Path
    phase: str
    result: Mapping[str, object]
    launch_instance_id: str | None = None


class LaunchAlreadyActiveError(ValueError):
    pass


class LaunchDaemonService:
    def __init__(
        self,
        root: str | Path = ".kairos/launches",
        *,
        target_resolver: "_LaunchTargetResolver | None" = None,
        target_factory: LaunchTargetFactory | None = None,
        command_dispatcher_factory: Callable[[Path], SystemCommandHandler] | None = None,
    ) -> None:
        self.root = Path(root).expanduser()
        if target_resolver is not None and target_factory is not None:
            raise TypeError("provide target_resolver or target_factory, not both")
        self._targets = target_resolver or _LaunchTargetResolver(target_factory)
        self._store = _LaunchDaemonStore()
        self._command_dispatcher_factory = command_dispatcher_factory

    def launch_foreground(
        self,
        *,
        mode: RuntimeMode | str,
        config_path: str | Path,
        launch_id: str | None = None,
        strategy_ref: str | None = None,
    ) -> LaunchDaemonResult:
        runtime_mode = mode if isinstance(mode, RuntimeMode) else RuntimeMode(str(mode))
        _validate_non_system_launch_id(launch_id)
        target = _resolve_target(self._targets, runtime_mode, Path(config_path), strategy_ref=strategy_ref)
        actual_launch_id = launch_id or target.launch_id
        _validate_non_system_launch_id(actual_launch_id)
        identity = _identity(actual_launch_id, runtime_mode, process_id=os.environ.get(_LAUNCH_INSTANCE_ID_ENV), root=self.root)
        group_directory = self.root / runtime_mode.value / actual_launch_id
        directory = _instance_directory(group_directory, identity)
        group_directory.mkdir(parents=True, exist_ok=True)
        with _LaunchGroupLock(group_directory):
            self._store.claim_start(group_directory, directory, identity)
        configured_launch_directory = str(target.launch_directory)
        target = _resolve_target(
            self._targets,
            runtime_mode,
            Path(config_path),
            strategy_ref=strategy_ref,
            launch_directory=directory,
        )
        mirror = (group_directory,) if group_directory != directory else ()
        self._store.write_state(
            directory,
            phase="starting",
            reason="started",
            identity=identity,
            context={"config_file": str(Path(config_path)), "configured_launch_directory": configured_launch_directory, "strategy_ref": strategy_ref},
            mirrors=mirror,
        )
        self._store.record_event(directory, "status", phase="starting", reason="started")
        try:
            self._store.write_state(
                directory,
                phase="running",
                reason="running",
                identity=identity,
                context={"config_file": str(Path(config_path)), "configured_launch_directory": configured_launch_directory, "strategy_ref": strategy_ref},
                mirrors=mirror,
            )
            self._store.record_event(directory, "status", phase="running", reason="running")
            target.bind_stop(lambda: _control_stop_requested(directory, self._command_dispatcher_factory))
            heartbeat = _LaunchHeartbeat(
                directory,
                dispatcher_factory=self._command_dispatcher_factory,
            )
            heartbeat.start()
            handlers = _install_process_stop_handlers(directory)
            try:
                with _launch_instance_environment(str(identity["process_id"])):
                    result = target.run()
            finally:
                _restore_process_stop_handlers(handlers)
                heartbeat.stop()
            summary = _launch_summary(
                actual_launch_id,
                runtime_mode,
                result,
                launch_instance_id=str(identity["process_id"]),
            )
            self._store.write_state(
                directory,
                phase="stopped",
                reason="target completed",
                identity=identity,
                context={"config_file": str(Path(config_path)), "configured_launch_directory": configured_launch_directory, "strategy_ref": strategy_ref},
                result=summary,
                mirrors=mirror,
            )
            self._store.write_summary(directory, summary | {"phase": "stopped", "status": "stopped", "reason": "target completed"})
            for mirror_directory in mirror:
                self._store.write_summary(mirror_directory, summary | {"phase": "stopped", "status": "stopped", "reason": "target completed"})
            self._store.record_event(directory, "status", phase="stopped", reason="target completed", result=summary)
            return LaunchDaemonResult(actual_launch_id, runtime_mode.value, directory, directory / "state.json", directory / "summary.json", "stopped", summary, str(identity["process_id"]))
        except Exception as error:
            summary = {
                "launch_id": actual_launch_id,
                "mode": runtime_mode.value,
                "launch_instance_id": str(identity["process_id"]),
                "phase": "failed",
                "status": "failed",
                "reason": f"{type(error).__name__}: {error}",
            }
            self._store.write_state(directory, phase="failed", reason=summary["reason"], identity=identity, result=summary, mirrors=mirror)
            self._store.write_summary(directory, summary)
            for mirror_directory in mirror:
                self._store.write_summary(mirror_directory, summary)
            self._store.record_event(directory, "status", phase="failed", reason=summary["reason"])
            raise

    def launch_system_foreground(self, *, launch_id: str = SYSTEM_LAUNCH_ID) -> LaunchDaemonResult:
        _validate_system_launch_id(launch_id)
        runtime_mode = RuntimeMode.SYSTEM
        identity = _identity(launch_id, runtime_mode, process_id=os.environ.get(_LAUNCH_INSTANCE_ID_ENV), root=self.root)
        group_directory = self.root / runtime_mode.value / launch_id
        directory = _instance_directory(group_directory, identity)
        group_directory.mkdir(parents=True, exist_ok=True)
        with _LaunchGroupLock(group_directory):
            self._store.claim_start(group_directory, directory, identity)
        mirror = (group_directory,)
        context = {"launch": "system", "builtin": True}
        self._store.write_state(directory, phase="starting", reason="started", identity=identity, context=context, mirrors=mirror)
        self._store.record_event(directory, "status", phase="starting", reason="started")
        try:
            self._store.write_state(directory, phase="running", reason="running", identity=identity, context=context, mirrors=mirror)
            self._store.record_event(directory, "status", phase="running", reason="running")
            heartbeat = _LaunchHeartbeat(
                directory,
                dispatcher_factory=self._command_dispatcher_factory,
            )
            heartbeat.start()
            handlers = _install_process_stop_handlers(directory)
            try:
                with _launch_instance_environment(str(identity["process_id"])):
                    result = self._targets.launch_system(launch_id=launch_id, launch_directory=directory)
            finally:
                _restore_process_stop_handlers(handlers)
                heartbeat.stop()
            summary = _launch_summary(
                launch_id,
                runtime_mode,
                result,
                launch_instance_id=str(identity["process_id"]),
            )
            self._store.write_state(directory, phase="stopped", reason="target completed", identity=identity, context=context, result=summary, mirrors=mirror)
            self._store.write_summary(directory, summary | {"phase": "stopped", "status": "stopped", "reason": "target completed"})
            for mirror_directory in mirror:
                self._store.write_summary(mirror_directory, summary | {"phase": "stopped", "status": "stopped", "reason": "target completed"})
            self._store.record_event(directory, "status", phase="stopped", reason="target completed", result=summary)
            return LaunchDaemonResult(launch_id, runtime_mode.value, directory, directory / "state.json", directory / "summary.json", "stopped", summary, str(identity["process_id"]))
        except Exception as error:
            summary = {
                "launch_id": launch_id,
                "mode": runtime_mode.value,
                "launch_instance_id": str(identity["process_id"]),
                "phase": "failed",
                "status": "failed",
                "reason": f"{type(error).__name__}: {error}",
            }
            self._store.write_state(directory, phase="failed", reason=summary["reason"], identity=identity, context=context, result=summary, mirrors=mirror)
            self._store.write_summary(directory, summary)
            for mirror_directory in mirror:
                self._store.write_summary(mirror_directory, summary)
            self._store.record_event(directory, "status", phase="failed", reason=summary["reason"])
            raise

    def start_background(
        self,
        *,
        mode: RuntimeMode | str,
        config_path: str | Path,
        launch_id: str | None = None,
        strategy_ref: str | None = None,
    ) -> LaunchDaemonResult:
        runtime_mode = mode if isinstance(mode, RuntimeMode) else RuntimeMode(str(mode))
        _validate_non_system_launch_id(launch_id)
        target = self._targets.describe(runtime_mode, Path(config_path))
        actual_launch_id = launch_id or target.launch_id
        _validate_non_system_launch_id(actual_launch_id)
        identity = _identity(actual_launch_id, runtime_mode, root=self.root)
        group_directory = self.root / runtime_mode.value / actual_launch_id
        directory = _instance_directory(group_directory, identity)
        group_directory.mkdir(parents=True, exist_ok=True)
        with _LaunchGroupLock(group_directory):
            self._store.claim_start(group_directory, directory, identity)
        log_path = directory / "daemon.log"
        context = {
            "config_file": str(Path(config_path)),
            "configured_launch_directory": target.launch_directory,
            "launch": "background",
            "strategy_ref": strategy_ref,
        }
        args = [
            sys.executable,
            "-m",
            "kairospy",
            "launch",
            "start",
            "--foreground",
            "--root",
            str(self.root),
            "--mode",
            runtime_mode.value,
            "--config",
            str(Path(config_path)),
        ]
        if launch_id is not None:
            args.extend(("--launch-id", launch_id))
        if strategy_ref is not None:
            args.extend(("--strategy", strategy_ref))
        identity = identity | {"argv": args}
        self._store.write_state(directory, phase="starting", reason="background launch requested", identity=identity, context=context, mirrors=(group_directory,))
        self._store.record_event(directory, "start_requested", phase="starting", reason="background launch requested", args=args, log_file=str(log_path))
        with log_path.open("ab") as output:
            env = os.environ.copy()
            env[_LAUNCH_INSTANCE_ID_ENV] = str(identity["process_id"])
            process = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, env=env)
        result = {"pid": process.pid, "args": args, "log_file": str(log_path)}
        self._store.write_state(directory, phase="starting", reason="background process launched", identity=identity, context=context, result=result, mirrors=(group_directory,))
        return LaunchDaemonResult(
            actual_launch_id,
            runtime_mode.value,
            directory,
            directory / "state.json",
            directory / "summary.json",
            "starting",
            result,
            str(identity["process_id"]),
        )

    def start_system_background(self, *, launch_id: str = SYSTEM_LAUNCH_ID) -> LaunchDaemonResult:
        _validate_system_launch_id(launch_id)
        runtime_mode = RuntimeMode.SYSTEM
        identity = _identity(launch_id, runtime_mode, root=self.root)
        group_directory = self.root / runtime_mode.value / launch_id
        directory = _instance_directory(group_directory, identity)
        group_directory.mkdir(parents=True, exist_ok=True)
        with _LaunchGroupLock(group_directory):
            self._store.claim_start(group_directory, directory, identity)
        log_path = directory / "daemon.log"
        context = {"launch": "system", "builtin": True, "background": True}
        args = [
            sys.executable,
            "-m",
            "kairospy",
            "system",
            "up",
            "--foreground",
            "--root",
            str(self.root),
            "--launch-id",
            launch_id,
        ]
        identity = identity | {"argv": args}
        self._store.write_state(directory, phase="starting", reason="background launch requested", identity=identity, context=context, mirrors=(group_directory,))
        self._store.record_event(directory, "start_requested", phase="starting", reason="background launch requested", args=args, log_file=str(log_path))
        with log_path.open("ab") as output:
            env = os.environ.copy()
            env[_LAUNCH_INSTANCE_ID_ENV] = str(identity["process_id"])
            process = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, env=env)
        result = {"pid": process.pid, "args": args, "log_file": str(log_path)}
        self._store.write_state(directory, phase="starting", reason="background process launched", identity=identity, context=context, result=result, mirrors=(group_directory,))
        return LaunchDaemonResult(launch_id, runtime_mode.value, directory, directory / "state.json", directory / "summary.json", "starting", result, str(identity["process_id"]))


class _LaunchTargetResolver:
    def __init__(self, factory: LaunchTargetFactory | None) -> None:
        self.factory = factory

    def describe(self, mode: RuntimeMode, config_path: Path) -> LaunchTargetDescriptor:
        if self.factory is None:
            raise TypeError("launch start requires a composition-provided target factory")
        return self.factory.describe(mode=mode, config_path=config_path)

    def resolve(
        self,
        mode: RuntimeMode,
        config_path: Path,
        *,
        strategy_ref: str | None = None,
        launch_directory: Path | None = None,
    ) -> LaunchTarget:
        try:
            if self.factory is None:
                raise TypeError("launch start requires a composition-provided target factory")
            return self.factory.resolve(
                LaunchRequest(
                    mode=mode,
                    config_path=config_path,
                    strategy_ref=strategy_ref,
                    launch_directory=launch_directory,
                )
            )
        except TradingConfigurationError:
            raise

    def launch_system(self, *, launch_id: str, launch_directory: Path) -> object:
        if self.factory is None:
            raise TypeError("system launch requires a composition-provided target factory")
        return self.factory.launch_system(launch_id=launch_id, launch_directory=launch_directory)


class _LaunchDaemonStore:
    def claim_start(self, group_directory: Path, directory: Path, identity: Mapping[str, object]) -> None:
        active = _active_current_instance(group_directory)
        if active is not None and active.get("launch_instance_id") != identity["process_id"]:
            raise LaunchAlreadyActiveError(
                f"launch already has an active instance: {identity['mode']}:{identity['launch_id']} "
                f"({active.get('launch_instance_id')})"
            )
        directory.mkdir(parents=True, exist_ok=True)
        self.write_current(group_directory, directory, identity)

    def write_current(self, group_directory: Path, directory: Path, identity: Mapping[str, object]) -> None:
        _write_json(
            group_directory / "current.json",
            {
                "launch_id": identity["launch_id"],
                "mode": identity["mode"],
                "launch_instance_id": identity["process_id"],
                "directory": str(directory),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def write_state(
        self,
        directory: Path,
        *,
        phase: str,
        reason: str,
        identity: Mapping[str, object],
        context: Mapping[str, object] | None = None,
        result: Mapping[str, object] | None = None,
        mirrors: tuple[Path, ...] = (),
    ) -> None:
        now = datetime.now(timezone.utc)
        payload = {
            "launch_id": identity["launch_id"],
            "mode": identity["mode"],
            "launch_instance_id": identity["process_id"],
            "phase": phase,
            "status": phase,
            "reason": reason,
            "desired_state": "stopped" if phase in {"stopped", "failed"} else "running",
            "identity": dict(identity),
            "heartbeat_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "context": dict(context or {}),
            "result": dict(result or {}),
        }
        _write_json(directory / "state.json", payload)
        for mirror in mirrors:
            _write_json(mirror / "state.json", payload | {"mirrored_from": str(directory)})

    def write_summary(self, directory: Path, summary: Mapping[str, object]) -> None:
        _write_json(directory / "summary.json", summary)

    def record_event(self, directory: Path, event_type: str, **payload: object) -> None:
        event = {
            "time": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            **payload,
        }
        with (directory / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_jsonable(event), sort_keys=True) + "\n")
        group_directory = directory.parent.parent if directory.parent.name == "instances" else None
        if group_directory is not None and group_directory.exists():
            with (group_directory / "events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_jsonable(event), sort_keys=True) + "\n")


def _process_runtime_commands(directory: Path, dispatcher_factory: Callable[[Path], SystemCommandHandler] | None) -> None:
    if dispatcher_factory is None:
        return
    queue = SystemCommandFileQueue(directory)
    dispatcher = dispatcher_factory(directory)
    for command in queue.pending():
        result = dispatcher.dispatch(command)
        if command.kind == "runtime.stop" and result.status == "accepted":
            _mirror_runtime_stop(directory, command, result.result)
        queue.respond(result)


def _control_stop_requested(directory: Path, dispatcher_factory: Callable[[Path], SystemCommandHandler] | None = None) -> bool:
    _process_runtime_commands(directory, dispatcher_factory)
    command = _read_json(directory / "command.json")
    requested = str(command.get("desired_state") or "").strip().lower() == "stopped"
    if requested:
        _mark_stopping(directory, reason=str(command.get("reason") or "stop requested"))
    return requested


def _request_stop_command(directory: Path, *, reason: str, actor: str) -> None:
    command = SystemCommandFileQueue(directory).submit(
        "runtime.stop",
        {"desired_state": "stopped", "reason": reason},
        actor=actor,
    )
    _mirror_runtime_stop(
        directory,
        command,
        {"reason": reason},
    )


def _install_process_stop_handlers(directory: Path) -> dict[int, object]:
    if threading.current_thread() is not threading.main_thread():
        return {}
    previous: dict[int, object] = {}
    interrupts = 0

    def handle(signum: int, _frame: object) -> None:
        nonlocal interrupts
        interrupts += 1
        if interrupts >= 2:
            raise KeyboardInterrupt
        _request_stop_command(
            directory,
            reason=f"received signal {signum}",
            actor="signal",
        )

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, handle)
    return previous


def _restore_process_stop_handlers(previous: Mapping[int, object]) -> None:
    if not previous or threading.current_thread() is not threading.main_thread():
        return
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def _mark_stopping(directory: Path, *, reason: str) -> None:
    state = _read_json(directory / "state.json")
    phase = str(state.get("phase") or "").strip().lower()
    if phase not in _ACTIVE_PHASES or phase == "stopping":
        return
    now = datetime.now(timezone.utc).isoformat()
    payload = state | {
        "phase": "stopping",
        "status": "stopping",
        "desired_state": "stopped",
        "reason": reason,
        "heartbeat_at": now,
        "updated_at": now,
    }
    _write_json(directory / "state.json", payload)
    if directory.parent.name == "instances":
        mirror = directory.parent.parent
        if mirror.exists():
            _write_json(mirror / "state.json", payload | {"mirrored_from": str(directory)})


def _mirror_runtime_stop(directory: Path, command: SystemCommand, result: Mapping[str, object]) -> None:
    reason = str(result.get("reason") or command.payload.get("reason") or "requested by system command")
    _write_json(
        directory / "command.json",
        {
            "command_id": command.command_id,
            "kind": command.kind,
            "desired_state": "stopped",
            "reason": reason,
            "actor": command.actor,
            "requested_at": command.requested_at,
        },
    )


def _resolve_target(
    targets: object,
    mode: RuntimeMode,
    config_path: Path,
    *,
    strategy_ref: str | None,
    launch_directory: Path | None = None,
) -> LaunchTarget:
    resolve = getattr(targets, "resolve")
    try:
        return resolve(mode, config_path, strategy_ref=strategy_ref, launch_directory=launch_directory)
    except TypeError:
        if strategy_ref is not None or launch_directory is not None:
            raise
        return resolve(mode, config_path)


def _validate_non_system_launch_id(launch_id: str | None) -> None:
    if launch_id is not None and launch_id in RESERVED_LAUNCH_IDS:
        raise ValueError(f"launch id {launch_id!r} is reserved for the built-in system runtime")


def _validate_system_launch_id(launch_id: str) -> None:
    if launch_id != SYSTEM_LAUNCH_ID:
        raise ValueError(f"system launch id is fixed: {SYSTEM_LAUNCH_ID}")


@contextmanager
def _launch_instance_environment(instance_id: str):
    """Expose the control-plane identity to legacy runtime composition code."""

    previous = os.environ.get(_LAUNCH_INSTANCE_ID_ENV)
    os.environ[_LAUNCH_INSTANCE_ID_ENV] = instance_id
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(_LAUNCH_INSTANCE_ID_ENV, None)
        else:
            os.environ[_LAUNCH_INSTANCE_ID_ENV] = previous


def _launch_summary(
    launch_id: str,
    mode: RuntimeMode,
    result: object,
    *,
    launch_instance_id: str | None = None,
) -> dict[str, object]:
    runtime = getattr(result, "runtime", None)
    return _jsonable(
        {
            "launch_id": launch_id,
            "mode": mode.value,
            "launch_instance_id": launch_instance_id,
            "strategy_id": getattr(runtime, "program_id", None),
            "event_count": getattr(runtime, "event_count", None),
            "intent_count": _intent_count(result),
            "fills": len(tuple(getattr(result, "fills", ()) or ())),
            "closed_trades": len(tuple(getattr(result, "trades", ()) or ())),
            "initial_equity": getattr(result, "initial_equity", None),
            "final_equity": getattr(result, "final_equity", None),
            "net_profit": getattr(result, "net_profit", None),
            "total_return": getattr(result, "total_return", None),
            "metrics": getattr(result, "metrics", {}),
        }
    )


def _intent_count(result: object) -> int | None:
    intents = getattr(result, "intents", None)
    listing = getattr(intents, "list", None)
    return len(listing()) if callable(listing) else None


def _identity(launch_id: str, mode: RuntimeMode, *, process_id: str | None = None, root: Path | None = None) -> dict[str, object]:
    return {
        "launch_id": launch_id,
        "mode": mode.value,
        "process_id": process_id if process_id is not None and process_id.strip() else str(uuid4()),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "host": socket.gethostname(),
        "cwd": str(Path.cwd()),
        "root": None if root is None else str(root),
        "argv": list(sys.argv),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


def _instance_directory(group_directory: Path, identity: Mapping[str, object]) -> Path:
    return group_directory / "instances" / str(identity["process_id"])


class _LaunchGroupLock:
    def __init__(self, group_directory: Path) -> None:
        self.path = group_directory / "launch.lock"
        self._file = None

    def __enter__(self) -> "_LaunchGroupLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+", encoding="utf-8")
        fcntl.flock(self._file.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool | None:
        if self._file is not None:
            try:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            finally:
                self._file.close()
        return None


class _LaunchHeartbeat:
    def __init__(
        self,
        directory: Path,
        *,
        interval_seconds: float | None = None,
        dispatcher_factory: Callable[[Path], SystemCommandHandler] | None = None,
    ) -> None:
        self.directory = directory
        self.interval_seconds = _HEARTBEAT_INTERVAL_SECONDS if interval_seconds is None else interval_seconds
        self.dispatcher_factory = dispatcher_factory
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"kairos-launch-heartbeat-{directory.name}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self.interval_seconds + 0.5)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            _process_runtime_commands(self.directory, self.dispatcher_factory)
            state = _read_json(self.directory / "state.json")
            phase = str(state.get("phase") or "").strip().lower()
            if phase not in _ACTIVE_PHASES:
                return
            now = datetime.now(timezone.utc).isoformat()
            _write_json(self.directory / "state.json", state | {"heartbeat_at": now, "updated_at": now})


def _active_current_instance(group_directory: Path) -> Mapping[str, object] | None:
    current = _read_json(group_directory / "current.json")
    directory_value = current.get("directory")
    instance_id = current.get("launch_instance_id")
    if isinstance(directory_value, str) and directory_value.strip():
        directory = Path(directory_value)
        if not directory.is_absolute():
            directory = group_directory / directory
    elif isinstance(instance_id, str) and instance_id.strip():
        directory = group_directory / "instances" / instance_id
    else:
        return None
    state = _read_json(directory / "state.json")
    phase = str(state.get("phase") or "").strip().lower()
    if not phase:
        updated_at = _parse_time(current.get("updated_at"))
        if updated_at is not None and (datetime.now(timezone.utc) - updated_at).total_seconds() > _STALE_AFTER_SECONDS:
            return None
        return current | {"directory": str(directory)}
    if phase not in _ACTIVE_PHASES:
        return None
    heartbeat_at = _parse_time(state.get("heartbeat_at"))
    if heartbeat_at is None:
        return current | {"directory": str(directory)}
    age = (datetime.now(timezone.utc) - heartbeat_at).total_seconds()
    if age > _STALE_AFTER_SECONDS:
        _write_json(directory / "state.json", state | {"phase": "abandoned", "status": "abandoned", "reason": "stale heartbeat abandoned"})
        return None
    return current | {"directory": str(directory)}


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Mapping[str, object]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


__all__ = ["LaunchAlreadyActiveError", "LaunchDaemonResult", "LaunchDaemonService"]
