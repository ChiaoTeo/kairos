from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
import fcntl
import json
from pathlib import Path
import socket
import os
import subprocess
import sys
import threading
from typing import Callable, Mapping
from uuid import uuid4

from kairospy.application.modes import RuntimeMode
from kairospy.application.service.modes.backtest import BacktestConfigurationError, ConfiguredBacktest, configured_backtest
from kairospy.application.service.modes.live import ConfiguredLive, LiveConfigurationError, configured_live
from kairospy.application.service.modes.paper import ConfiguredPaper, PaperConfigurationError, configured_paper
from kairospy.application.launch.launcher import TradingSystemLauncher
from kairospy.application.system.session import SystemCommand, SystemCommandDispatcher, SystemCommandFileQueue
from kairospy.config import ConfigError, RESERVED_LAUNCH_IDS, SYSTEM_LAUNCH_ID, load_launch_config


_TRADING_LAUNCHER = TradingSystemLauncher()
_LAUNCH_INSTANCE_ID_ENV = "KAIROS_LAUNCH_INSTANCE_ID"
_ACTIVE_PHASES = {"starting", "running", "stopping"}
_STALE_AFTER_SECONDS = 5.0
_HEARTBEAT_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class _LaunchDaemonTarget:
    configured: object
    runner: Callable[[], object]

    @property
    def launch_id(self) -> str:
        return str(getattr(self.configured, "launch_id"))

    @property
    def launch_directory(self) -> str:
        return str(getattr(self.configured, "launch_directory", ""))


@dataclass(frozen=True, slots=True)
class _LaunchTargetDescriptor:
    launch_id: str
    launch_directory: str


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
    def __init__(self, root: str | Path = ".kairos/launches", *, target_resolver: "_LaunchTargetResolver | None" = None) -> None:
        self.root = Path(root).expanduser()
        self._targets = target_resolver or _LaunchTargetResolver(_TRADING_LAUNCHER)
        self._store = _LaunchDaemonStore()

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
        identity = _identity(actual_launch_id, runtime_mode, process_id=os.environ.get(_LAUNCH_INSTANCE_ID_ENV))
        group_directory = self.root / runtime_mode.value / actual_launch_id
        directory = _instance_directory(group_directory, identity)
        group_directory.mkdir(parents=True, exist_ok=True)
        with _LaunchGroupLock(group_directory):
            self._store.claim_start(group_directory, directory, identity)
        configured_launch_directory = target.launch_directory
        target = _target_with_instance_directory(target, directory, _TRADING_LAUNCHER)
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
            _bind_stop_control(target.configured, directory)
            heartbeat = _LaunchHeartbeat(directory)
            heartbeat.start()
            try:
                result = target.runner()
            finally:
                heartbeat.stop()
            summary = _launch_summary(actual_launch_id, runtime_mode, result)
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
        identity = _identity(launch_id, runtime_mode, process_id=os.environ.get(_LAUNCH_INSTANCE_ID_ENV))
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
            heartbeat = _LaunchHeartbeat(directory)
            heartbeat.start()
            try:
                result = _TRADING_LAUNCHER.launch_app_system(launch_id=launch_id, launch_directory=directory)
            finally:
                heartbeat.stop()
            summary = _launch_summary(launch_id, runtime_mode, result)
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
        identity = _identity(actual_launch_id, runtime_mode)
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
            "daemon",
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
        identity = _identity(launch_id, runtime_mode)
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
            "launch",
            "system",
            "up",
            "--foreground",
            "--root",
            str(self.root),
            "--launch-id",
            launch_id,
        ]
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
    def __init__(self, launcher: TradingSystemLauncher) -> None:
        self.launcher = launcher

    def describe(self, mode: RuntimeMode, config_path: Path) -> _LaunchTargetDescriptor:
        try:
            launch_config = load_launch_config(config_path)
            launch_config.require_mode(mode.value)
        except ConfigError as error:
            raise ValueError(str(error)) from error
        mode_config = launch_config.values.get(mode.value)
        if not isinstance(mode_config, Mapping):
            mode_config = {}
        return _LaunchTargetDescriptor(
            launch_id=launch_config.launch_id,
            launch_directory=str(_configured_launch_directory(mode, mode_config, root=launch_config.root, launch_id=launch_config.launch_id)),
        )

    def resolve(self, mode: RuntimeMode, config_path: Path, *, strategy_ref: str | None = None) -> _LaunchDaemonTarget:
        if mode is RuntimeMode.BACKTEST:
            try:
                configured = configured_backtest(config_path, strategy_ref=strategy_ref)
            except BacktestConfigurationError:
                raise
            return _LaunchDaemonTarget(configured, lambda: self.launcher.launch_configured_backtest(configured))
        if mode is RuntimeMode.PAPER:
            try:
                configured = configured_paper(config_path, account_resolver=self.launcher._account_resolver(config_path), strategy_ref=strategy_ref)
            except PaperConfigurationError:
                raise
            return _LaunchDaemonTarget(configured, lambda: self.launcher.launch_configured_paper(configured))
        if mode is RuntimeMode.LIVE:
            try:
                configured = configured_live(config_path, account_resolver=self.launcher._account_resolver(config_path), strategy_ref=strategy_ref)
            except LiveConfigurationError:
                raise
            return _LaunchDaemonTarget(configured, lambda: self.launcher.launch_configured_live(configured))
        raise ValueError("daemon foreground start supports backtest, paper, and live config targets")


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


def _bind_stop_control(configured: object, directory: Path) -> None:
    market_data = getattr(configured, "market_data", None)
    setter = getattr(market_data, "set_stop_requested", None)
    if callable(setter):
        setter(lambda: _control_stop_requested(directory))


def _control_stop_requested(directory: Path) -> bool:
    _process_runtime_commands(directory)
    command = _read_json(directory / "command.json")
    return str(command.get("desired_state") or "").strip().lower() == "stopped"


def _process_runtime_commands(directory: Path) -> None:
    queue = SystemCommandFileQueue(directory)
    dispatcher = SystemCommandDispatcher(directory)
    for command in queue.pending():
        result = dispatcher.dispatch(command)
        if command.kind == "runtime.stop" and result.status == "accepted":
            _mirror_runtime_stop(directory, command, result.result)
        queue.respond(result)


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


def _resolve_target(targets: object, mode: RuntimeMode, config_path: Path, *, strategy_ref: str | None) -> _LaunchDaemonTarget:
    resolve = getattr(targets, "resolve")
    try:
        return resolve(mode, config_path, strategy_ref=strategy_ref)
    except TypeError:
        if strategy_ref is not None:
            raise
        return resolve(mode, config_path)


def _validate_non_system_launch_id(launch_id: str | None) -> None:
    if launch_id is not None and launch_id in RESERVED_LAUNCH_IDS:
        raise ValueError(f"launch id {launch_id!r} is reserved for the built-in system runtime")


def _validate_system_launch_id(launch_id: str) -> None:
    if launch_id != SYSTEM_LAUNCH_ID:
        raise ValueError(f"system launch id is fixed: {SYSTEM_LAUNCH_ID}")


def _target_with_instance_directory(target: _LaunchDaemonTarget, directory: Path, launcher: TradingSystemLauncher) -> _LaunchDaemonTarget:
    configured = target.configured
    if isinstance(configured, ConfiguredBacktest):
        instance_configured = replace(configured, launch_directory=directory)
        return _LaunchDaemonTarget(instance_configured, lambda: launcher.launch_configured_backtest(instance_configured))
    if isinstance(configured, ConfiguredPaper):
        instance_configured = replace(configured, launch_directory=directory)
        return _LaunchDaemonTarget(instance_configured, lambda: launcher.launch_configured_paper(instance_configured))
    if isinstance(configured, ConfiguredLive):
        instance_configured = replace(configured, launch_directory=directory, state_path=directory / "live_state.json")
        return _LaunchDaemonTarget(instance_configured, lambda: launcher.launch_configured_live(instance_configured))
    return target


def _launch_summary(launch_id: str, mode: RuntimeMode, result: object) -> dict[str, object]:
    runtime = getattr(result, "runtime", None)
    return _jsonable(
        {
            "launch_id": launch_id,
            "mode": mode.value,
            "strategy_id": getattr(runtime, "strategy_id", None),
            "event_count": getattr(runtime, "event_count", None),
            "intent_count": getattr(runtime, "intent_count", None),
            "fills": len(tuple(getattr(result, "fills", ()) or ())),
            "closed_trades": len(tuple(getattr(result, "trades", ()) or ())),
            "decision_trace_count": len(tuple(getattr(result, "decision_trace", ()) or ())),
            "risk_snapshot_count": len(tuple(getattr(result, "risk_snapshots", ()) or ())),
            "initial_equity": getattr(result, "initial_equity", None),
            "final_equity": getattr(result, "final_equity", None),
            "net_profit": getattr(result, "net_profit", None),
            "total_return": getattr(result, "total_return", None),
            "metrics": getattr(result, "metrics", {}),
        }
    )


def _configured_launch_directory(mode: RuntimeMode, mode_config: Mapping[str, object], *, root: Path, launch_id: str) -> Path:
    launches_root_value = mode_config.get("launches_root")
    launches_root = Path(".kairos/launches").resolve() if launches_root_value is None else _resolve_workspace_path(launches_root_value, root=root)
    return launches_root / mode.value / launch_id


def _resolve_workspace_path(value: object, *, root: Path) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _identity(launch_id: str, mode: RuntimeMode, *, process_id: str | None = None) -> dict[str, object]:
    return {
        "launch_id": launch_id,
        "mode": mode.value,
        "process_id": process_id if process_id is not None and process_id.strip() else str(uuid4()),
        "pid": os.getpid(),
        "host": socket.gethostname(),
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
    def __init__(self, directory: Path, *, interval_seconds: float | None = None) -> None:
        self.directory = directory
        self.interval_seconds = _HEARTBEAT_INTERVAL_SECONDS if interval_seconds is None else interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"kairos-launch-heartbeat-{directory.name}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self.interval_seconds + 0.5)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            _process_runtime_commands(self.directory)
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
