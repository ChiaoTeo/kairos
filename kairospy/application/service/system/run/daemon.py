from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
import json
from pathlib import Path
import socket
import os
import subprocess
import sys
from typing import Callable, Mapping
from uuid import uuid4

from kairospy.application.runtime import RuntimeMode
from kairospy.application.service.modes.backtest import BacktestConfigurationError, configured_backtest
from kairospy.application.service.modes.live import LiveConfigurationError, configured_live
from kairospy.application.service.modes.paper import PaperConfigurationError, configured_paper


@dataclass(frozen=True, slots=True)
class RunDaemonResult:
    run_id: str
    mode: str
    directory: Path
    state_path: Path
    summary_path: Path
    phase: str
    result: Mapping[str, object]
    run_instance_id: str | None = None


class RunDaemonService:
    def __init__(self, root: str | Path = ".kairos/runs") -> None:
        self.root = Path(root).expanduser()

    def run_foreground(
        self,
        *,
        mode: RuntimeMode | str,
        config_path: str | Path,
        run_id: str | None = None,
    ) -> RunDaemonResult:
        runtime_mode = mode if isinstance(mode, RuntimeMode) else RuntimeMode(str(mode))
        configured, runner = _target(runtime_mode, Path(config_path))
        actual_run_id = run_id or str(getattr(configured, "run_id"))
        identity = _identity(actual_run_id, runtime_mode)
        group_directory = self.root / runtime_mode.value / actual_run_id
        directory = _instance_directory(group_directory, identity)
        directory.mkdir(parents=True, exist_ok=True)
        group_directory.mkdir(parents=True, exist_ok=True)
        _write_current(group_directory, directory, identity)
        mirror = (group_directory,) if group_directory != directory else ()
        self._write_state(
            directory,
            phase="starting",
            reason="started",
            identity=identity,
            context={"config_file": str(Path(config_path)), "configured_run_directory": str(getattr(configured, "run_directory", ""))},
            mirrors=mirror,
        )
        self._record_event(directory, "status", phase="starting", reason="started")
        try:
            self._write_state(
                directory,
                phase="running",
                reason="running",
                identity=identity,
                context={"config_file": str(Path(config_path)), "configured_run_directory": str(getattr(configured, "run_directory", ""))},
                mirrors=mirror,
            )
            self._record_event(directory, "status", phase="running", reason="running")
            _bind_stop_control(configured, directory)
            result = runner()
            summary = _run_summary(actual_run_id, runtime_mode, result)
            self._write_state(
                directory,
                phase="stopped",
                reason="target completed",
                identity=identity,
                context={"config_file": str(Path(config_path)), "configured_run_directory": str(getattr(configured, "run_directory", ""))},
                result=summary,
                mirrors=mirror,
            )
            self._write_summary(directory, summary | {"phase": "stopped", "status": "stopped", "reason": "target completed"})
            for mirror_directory in mirror:
                self._write_summary(mirror_directory, summary | {"phase": "stopped", "status": "stopped", "reason": "target completed"})
            self._record_event(directory, "status", phase="stopped", reason="target completed", result=summary)
            return RunDaemonResult(actual_run_id, runtime_mode.value, directory, directory / "state.json", directory / "summary.json", "stopped", summary, str(identity["process_id"]))
        except Exception as error:
            summary = {
                "run_id": actual_run_id,
                "mode": runtime_mode.value,
                "phase": "failed",
                "status": "failed",
                "reason": f"{type(error).__name__}: {error}",
            }
            self._write_state(directory, phase="failed", reason=summary["reason"], identity=identity, result=summary, mirrors=mirror)
            self._write_summary(directory, summary)
            for mirror_directory in mirror:
                self._write_summary(mirror_directory, summary)
            self._record_event(directory, "status", phase="failed", reason=summary["reason"])
            raise

    def start_background(
        self,
        *,
        mode: RuntimeMode | str,
        config_path: str | Path,
        run_id: str | None = None,
    ) -> RunDaemonResult:
        runtime_mode = mode if isinstance(mode, RuntimeMode) else RuntimeMode(str(mode))
        configured, _runner = _target(runtime_mode, Path(config_path))
        actual_run_id = run_id or str(getattr(configured, "run_id"))
        identity = _identity(actual_run_id, runtime_mode)
        group_directory = self.root / runtime_mode.value / actual_run_id
        directory = _instance_directory(group_directory, identity)
        directory.mkdir(parents=True, exist_ok=True)
        group_directory.mkdir(parents=True, exist_ok=True)
        _write_current(group_directory, directory, identity)
        log_path = directory / "daemon.log"
        context = {
            "config_file": str(Path(config_path)),
            "configured_run_directory": str(getattr(configured, "run_directory", "")),
            "launch": "background",
        }
        args = [
            sys.executable,
            "-m",
            "kairospy",
            "run",
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
        if run_id is not None:
            args.extend(("--run-id", run_id))
        self._write_state(directory, phase="starting", reason="background launch requested", identity=identity, context=context, mirrors=(group_directory,))
        self._record_event(directory, "start_requested", phase="starting", reason="background launch requested", args=args, log_file=str(log_path))
        with log_path.open("ab") as output:
            process = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        result = {"pid": process.pid, "args": args, "log_file": str(log_path)}
        self._write_state(directory, phase="starting", reason="background process launched", identity=identity, context=context, result=result, mirrors=(group_directory,))
        return RunDaemonResult(
            actual_run_id,
            runtime_mode.value,
            directory,
            directory / "state.json",
            directory / "summary.json",
            "starting",
            result,
            str(identity["process_id"]),
        )

    def _write_state(
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
            "run_id": identity["run_id"],
            "mode": identity["mode"],
            "run_instance_id": identity["process_id"],
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

    def _write_summary(self, directory: Path, summary: Mapping[str, object]) -> None:
        _write_json(directory / "summary.json", summary)

    def _record_event(self, directory: Path, event_type: str, **payload: object) -> None:
        event = {
            "time": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            **payload,
        }
        with (directory / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_jsonable(event), sort_keys=True) + "\n")
        group_directory = directory.parent.parent if directory.parent.name == "runs" else None
        if group_directory is not None and group_directory.exists():
            with (group_directory / "events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_jsonable(event), sort_keys=True) + "\n")


def _target(mode: RuntimeMode, config_path: Path) -> tuple[object, Callable[[], object]]:
    if mode is RuntimeMode.BACKTEST:
        try:
            configured = configured_backtest(config_path)
        except BacktestConfigurationError:
            raise
        return configured, configured.run
    if mode is RuntimeMode.PAPER:
        try:
            configured = configured_paper(config_path)
        except PaperConfigurationError:
            raise
        return configured, configured.run
    if mode is RuntimeMode.LIVE:
        try:
            configured = configured_live(config_path)
        except LiveConfigurationError:
            raise
        return configured, configured.run
    raise ValueError("daemon foreground start supports backtest, paper, and live config targets")


def _bind_stop_control(configured: object, directory: Path) -> None:
    market_data = getattr(configured, "market_data", None)
    setter = getattr(market_data, "set_stop_requested", None)
    if callable(setter):
        setter(lambda: _control_stop_requested(directory))


def _control_stop_requested(directory: Path) -> bool:
    command = _read_json(directory / "command.json")
    return str(command.get("desired_state") or "").strip().lower() == "stopped"


def _run_summary(run_id: str, mode: RuntimeMode, result: object) -> dict[str, object]:
    runtime = getattr(result, "runtime", None)
    return _jsonable(
        {
            "run_id": run_id,
            "mode": mode.value,
            "strategy_id": getattr(runtime, "strategy_id", None),
            "event_count": getattr(runtime, "event_count", None),
            "intent_count": getattr(runtime, "intent_count", None),
            "fills": len(tuple(getattr(result, "fills", ()) or ())),
            "closed_trades": len(tuple(getattr(result, "trades", ()) or ())),
            "initial_equity": getattr(result, "initial_equity", None),
            "final_equity": getattr(result, "final_equity", None),
            "net_profit": getattr(result, "net_profit", None),
            "total_return": getattr(result, "total_return", None),
            "metrics": getattr(result, "metrics", {}),
        }
    )


def _identity(run_id: str, mode: RuntimeMode) -> dict[str, object]:
    return {
        "run_id": run_id,
        "mode": mode.value,
        "process_id": str(uuid4()),
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


def _instance_directory(group_directory: Path, identity: Mapping[str, object]) -> Path:
    return group_directory / "runs" / str(identity["process_id"])


def _write_current(group_directory: Path, directory: Path, identity: Mapping[str, object]) -> None:
    _write_json(
        group_directory / "current.json",
        {
            "run_id": identity["run_id"],
            "mode": identity["mode"],
            "run_instance_id": identity["process_id"],
            "directory": str(directory),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


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


__all__ = ["RunDaemonResult", "RunDaemonService"]
