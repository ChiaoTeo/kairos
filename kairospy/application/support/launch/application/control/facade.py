from __future__ import annotations

import os
import json
import signal
import shlex
import subprocess
import time
from pathlib import Path
from typing import Mapping

from kairospy.application.support.launch.application.control import LaunchControl
from kairospy.application.support.launch.application.control.daemon import LaunchAlreadyActiveError
from kairospy.application.support.launch.application.launcher import TradingConfigurationError
from kairospy.application.support.launch.application.protocol import LaunchTargetFactory
from kairospy.application.support.launch.domain.modes import RuntimeMode
from kairospy.application.usecases.workspace.application.context import workspace as resolve_workspace
from kairospy.application.system.application.runtime import TradingSystemSession
from kairospy.application.support.launch.application.configuration import SYSTEM_LAUNCH_ID, load_launch_config
from kairospy.application.usecases.account.application.configuration import AccountStore


DEFAULT_SYSTEM_LAUNCH_ID = SYSTEM_LAUNCH_ID


class LaunchApplication:
    def __init__(
        self,
        *,
        target_factory: LaunchTargetFactory | None = None,
    ) -> None:
        self._target_factory = target_factory

    def _control(self, root: str | Path) -> LaunchControl:
        return LaunchControl(root, target_factory=self._target_factory)

    def config(self, *, action: str, path: Path) -> dict[str, object]:
        launch_config = load_launch_config(path)
        if action == "validate":
            report = launch_config.validation_report()
            return {"path": str(report.path), "valid": report.valid, "issues": list(report.issues)}
        if action == "explain":
            return launch_config.explain()
        raise ValueError(f"unsupported config action: {action}")

    def register(self, *, name: str, config_path: Path) -> dict[str, object]:
        workspace = resolve_workspace()
        entry = workspace.launch_index.register(name, config_path)
        workspace.operations.append("launch.register", target={"launch": entry.name}, payload={"config": entry.config_path, "index": workspace.launch_index.path})
        return {"name": entry.name, "config": str(entry.config_path), "index": str(workspace.launch_index.path)}

    def register_target(self, *, name_or_config_path: str, config_path: Path | None) -> dict[str, object]:
        if config_path is not None:
            return self.register(name=name_or_config_path, config_path=config_path)
        path = Path(name_or_config_path)
        return self.register(name=load_launch_config(path).launch_id, config_path=path)

    def unregister(self, name: str) -> dict[str, object]:
        workspace = resolve_workspace()
        entry = workspace.launch_index.unregister(name)
        workspace.operations.append("launch.unregister", target={"launch": entry.name}, payload={"config": entry.config_path, "index": workspace.launch_index.path})
        return {"name": entry.name, "config": str(entry.config_path), "index": str(workspace.launch_index.path), "removed": True}

    def specs(self) -> dict[str, object]:
        return resolve_workspace().launch_index.to_dict()

    def list(self) -> dict[str, object]:
        workspace = resolve_workspace()
        launches = []
        registered_keys: set[tuple[str, str]] = set()
        for entry in workspace.launch_index.list():
            payload: dict[str, object] = {
                "name": entry.name,
                "source": "registered",
                "config": str(entry.config_path),
                "registered_at": entry.registered_at,
                "last_instance": entry.last_instance,
            }
            try:
                launch_config = load_launch_config(entry.config_path)
            except Exception as error:
                payload["valid"] = False
                payload["error"] = str(error)
            else:
                payload.update(
                    {
                        "valid": True,
                        "mode": launch_config.mode,
                        "launch_id": launch_config.launch_id,
                        "strategy": launch_config.strategy,
                    }
                )
                registered_keys.add((str(launch_config.mode), str(launch_config.launch_id)))
            launches.append(payload)
        for payload in _runtime_target_payloads(workspace.launch_root, registered_keys=registered_keys):
            launches.append(payload)
        return {"launches": launches, "count": len(launches), "path": str(workspace.launch_index.path)}

    def validate(self, target: str) -> dict[str, object]:
        workspace = resolve_workspace()
        path = workspace.launch_index.resolve_config_path(target)
        report = load_launch_config(path).validation_report()
        return {"target": target, "path": str(report.path), "valid": report.valid, "issues": list(report.issues)}

    def explain(self, target: str) -> dict[str, object]:
        workspace = resolve_workspace()
        path = workspace.launch_index.resolve_config_path(target)
        launch_config = load_launch_config(path)
        account_ref = launch_config.account_ref
        account_source = None
        if account_ref:
            try:
                account_source = str(AccountStore.load(workspace.accounts_root).get(account_ref).source_path)
            except Exception:
                account_source = None
        return {
            "target": target,
            "path": str(path),
            "launch_config": launch_config.explain(),
            "mode": launch_config.mode,
            "launch_id": launch_config.launch_id,
            "strategy": launch_config.strategy,
            "account_ref": account_ref,
            "sources": {
                "launch_config": str(launch_config.path) if launch_config.path is not None else None,
                "workspace_manifest": str(workspace.manifest_path) if workspace.manifest_path is not None else None,
                "account": account_source,
            },
        }

    def start(self, target: str, *, strategy_ref: str | None = None) -> object:
        mode, path = self.launch_target(target)
        result = self._control(self.launch_root(None)).launch_foreground(mode=mode, config_path=path, strategy_ref=strategy_ref)
        return _daemon_result_payload(result)

    def stop(self, *, target: str | None, mode: RuntimeMode | None, launch_id: str | None, root: Path | None) -> dict[str, object]:
        resolved_mode, resolved_launch_id = self.launch_identity(target, mode=mode, launch_id=launch_id, root=root, require_mode=False)
        if resolved_launch_id is None:
            raise ValueError("launch command requires a launch id when target is not a registered launch/config")
        control = self._control(self.launch_root(root))
        if resolved_mode is None:
            resolved_mode = _infer_mode_for_launch_id(control, resolved_launch_id)
        path = control.request_stop(mode=resolved_mode, launch_id=resolved_launch_id, reason="requested by cli")
        resolve_workspace().operations.append(
            "launch.stop",
            target={"mode": resolved_mode.value, "launch_id": resolved_launch_id},
            payload={"command_file": path},
        )
        return {"command_file": str(path), "mode": resolved_mode.value, "launch_id": resolved_launch_id, "desired_state": "stopped"}

    def submit_command(
        self,
        *,
        target: str | None,
        mode: RuntimeMode | None,
        launch_id: str | None,
        root: Path | None,
        kind: str,
        payload: Mapping[str, object] | None = None,
        wait: bool = False,
        timeout_seconds: float = 5.0,
    ) -> dict[str, object]:
        launch_root = self.launch_root(root)
        resolved_mode, resolved_launch_id = self.launch_identity(target, mode=mode, launch_id=launch_id, root=root)
        result = LaunchControl(launch_root).submit_command(
            mode=resolved_mode.value,
            launch_id=resolved_launch_id,
            kind=kind,
            payload=payload,
            actor="cli",
        )
        resolve_workspace().operations.append(
            "launch.command",
            target={"mode": resolved_mode.value, "launch_id": resolved_launch_id, "kind": result["kind"]},
            payload={"command_file": result["command_file"], "response_file": result["response_file"]},
        )
        if wait:
            result = result | {"response": _wait_for_command_response(Path(str(result["response_file"])), timeout_seconds=timeout_seconds)}
        return result

    def records(
        self,
        *,
        target: str | None = None,
        mode: RuntimeMode | None = None,
        launch_id: str | None = None,
        root: Path | None = None,
        current: bool = False,
    ) -> tuple[object, ...]:
        launch_root = self.launch_root(root)
        resolved_mode, resolved_launch_id = self.launch_identity(target, mode=mode, launch_id=launch_id, root=root, require_mode=False)
        records = LaunchControl(launch_root).list(mode=resolved_mode, launch_id=resolved_launch_id)
        if current and len(records) > 1:
            selected = _select_current_record(records, launch_root=launch_root)
            if selected is not None:
                return (selected,)
        return records

    def logs(
        self,
        *,
        target: str | None,
        mode: RuntimeMode | None,
        launch_id: str | None,
        root: Path | None,
        limit: int,
    ) -> dict[str, object]:
        record = self.single_launch_record(target, mode=mode, launch_id=launch_id, root=root)
        path = _log_path(record)
        if path is None:
            return {"launch": record_payload(record), "log_file": None, "lines": []}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return {"launch": record_payload(record), "log_file": str(path), "lines": lines[-limit:]}

    def log_path(
        self,
        *,
        target: str | None,
        mode: RuntimeMode | None,
        launch_id: str | None,
        root: Path | None,
    ) -> Path | None:
        return _log_path(self.single_launch_record(target, mode=mode, launch_id=launch_id, root=root))

    def log_file(
        self,
        *,
        target: str | None,
        mode: RuntimeMode | None,
        launch_id: str | None,
        root: Path | None,
    ) -> Path:
        path = self.log_path(target=target, mode=mode, launch_id=launch_id, root=root)
        if path is None:
            raise ValueError("launch log was not found")
        return path

    def artifacts(
        self,
        *,
        target: str | None,
        mode: RuntimeMode | None,
        launch_id: str | None,
        root: Path | None,
    ) -> dict[str, object]:
        record = self.single_launch_record(target, mode=mode, launch_id=launch_id, root=root)
        files = [
            {"path": str(path), "name": path.name, "size": path.stat().st_size}
            for path in sorted(record.directory.rglob("*"))
            if path.is_file()
        ]
        return {"launch": record_payload(record), "directory": str(record.directory), "artifacts": files, "count": len(files)}

    def launch_events(self, *, strategy_path: str, events_path: Path, launch_id: str, mode: RuntimeMode) -> object:
        factory = self._require_target_factory()
        return factory.launch_events(strategy_path=strategy_path, events_path=events_path, launch_id=launch_id, mode=mode)

    def open_system_session(self, *, strategy_path: str, launch_id: str, mode: RuntimeMode) -> TradingSystemSession:
        factory = self._require_target_factory()
        return factory.open_system_session(strategy_path=strategy_path, launch_id=launch_id, mode=mode)

    def _require_target_factory(self) -> LaunchTargetFactory:
        if self._target_factory is None:
            raise TypeError("launch execution requires a composition-provided target factory")
        return self._target_factory

    def system_up(
        self,
        *,
        root: Path | None = None,
        launch_id: str = DEFAULT_SYSTEM_LAUNCH_ID,
        foreground: bool = False,
    ) -> dict[str, object]:
        control = self._control(self.launch_root(root))
        result = (
            control.launch_system_foreground(launch_id=launch_id)
            if foreground
            else control.start_system_background(launch_id=launch_id)
        )
        return _daemon_result_payload(result)

    def system_down(
        self,
        *,
        root: Path | None = None,
        launch_id: str = DEFAULT_SYSTEM_LAUNCH_ID,
    ) -> dict[str, object]:
        path = LaunchControl(self.launch_root(root)).request_stop(
            mode=RuntimeMode.SYSTEM,
            launch_id=launch_id,
            reason="requested by system down",
        )
        return {"command_file": str(path), "mode": RuntimeMode.SYSTEM.value, "launch_id": launch_id, "desired_state": "stopped"}

    def system_restart(
        self,
        *,
        root: Path | None = None,
        launch_id: str = DEFAULT_SYSTEM_LAUNCH_ID,
        foreground: bool = False,
        timeout_seconds: float = 10.0,
        clean_stale: bool = False,
    ) -> dict[str, object]:
        launch_root = self.launch_root(root)
        control = LaunchControl(launch_root)
        stop: Mapping[str, object] | None = None
        cleaned: Mapping[str, object] | None = None
        if any(_launch_record_active(record) for record in control.list(mode=RuntimeMode.SYSTEM, launch_id=launch_id)):
            path = control.request_stop(
                mode=RuntimeMode.SYSTEM,
                launch_id=launch_id,
                reason="requested by system restart",
            )
            stop = {"command_file": str(path), "mode": RuntimeMode.SYSTEM.value, "launch_id": launch_id, "desired_state": "stopped"}
            _wait_for_launch_inactive(control, mode=RuntimeMode.SYSTEM, launch_id=launch_id, timeout_seconds=timeout_seconds)
        elif clean_stale:
            cleaned = _clean_current_stale_system(self.system_inspect(root=launch_root, launch_id=launch_id), timeout_seconds=timeout_seconds)
        start = (
            control.launch_system_foreground(launch_id=launch_id)
            if foreground
            else control.start_system_background(launch_id=launch_id)
        )
        return {"action": "restart", "stopped": stop, "cleaned": cleaned, "started": _daemon_result_payload(start)}

    def system_command(
        self,
        *,
        kind: str,
        payload: Mapping[str, object] | None = None,
        root: Path | None = None,
        launch_id: str = DEFAULT_SYSTEM_LAUNCH_ID,
        wait: bool = True,
        timeout_seconds: float = 5.0,
    ) -> dict[str, object]:
        result = LaunchControl(self.launch_root(root)).submit_command(
            mode=RuntimeMode.SYSTEM,
            launch_id=launch_id,
            kind=kind,
            payload=dict(payload or {}),
            actor="cli",
        )
        if wait:
            result = result | {"response": _wait_for_command_response(Path(str(result["response_file"])), timeout_seconds=timeout_seconds)}
        return result

    def system_records(self, *, root: Path | None = None, launch_id: str = DEFAULT_SYSTEM_LAUNCH_ID) -> tuple[object, ...]:
        return LaunchControl(self.launch_root(root)).list(mode=RuntimeMode.SYSTEM, launch_id=launch_id)

    def system_inspect(self, *, root: Path | None = None, launch_id: str = DEFAULT_SYSTEM_LAUNCH_ID) -> dict[str, object]:
        launch_root = self.launch_root(root)
        records = tuple(_management_record_payload(record) for record in LaunchControl(launch_root).list(mode=RuntimeMode.SYSTEM, launch_id=launch_id))
        current = _read_json(launch_root / RuntimeMode.SYSTEM.value / launch_id / "current.json")
        current_directory = _current_directory(launch_root, launch_id, current)
        current_record = _record_for_directory(records, current_directory)
        identity = _mapping(current_record.get("identity")) if current_record is not None else {}
        pid = _optional_int(identity.get("pid"))
        pid_alive = None if pid is None else _pid_alive(pid)
        processes = _matching_system_processes(launch_root=launch_root, launch_id=launch_id)
        managed_processes = tuple(process for process in processes if pid is not None and process.get("pid") == pid)
        orphaned_processes = tuple(process for process in processes if pid is None or process.get("pid") != pid)
        health = _system_health(
            current_record=current_record,
            pid=pid,
            pid_alive=pid_alive,
            processes=processes,
            orphaned_processes=orphaned_processes,
        )
        return {
            "launch_id": launch_id,
            "mode": RuntimeMode.SYSTEM.value,
            "root": str(launch_root),
            "current": current,
            "current_directory": None if current_directory is None else str(current_directory),
            "current_record": current_record,
            "identity": dict(identity),
            "pid": pid,
            "pid_alive": pid_alive,
            "heartbeat_fresh": None if current_record is None else not bool(current_record.get("stale")),
            "health": health,
            "processes": {
                "matching": list(processes),
                "managed": list(managed_processes),
                "orphaned": list(orphaned_processes),
                "count": len(processes),
            },
            "records": list(records),
        }

    def system_log_file(self, *, root: Path | None = None, launch_id: str = DEFAULT_SYSTEM_LAUNCH_ID) -> Path:
        launch_root = self.launch_root(root)
        current = _read_json(launch_root / RuntimeMode.SYSTEM.value / launch_id / "current.json")
        current_directory = _current_directory(launch_root, launch_id, current)
        path = None if current_directory is None else _first_log_file(
            (
                current_directory / "daemon.log",
                current_directory / "launch.log",
            )
        )
        if path is None:
            raise ValueError("system launch log was not found")
        return path

    def daemon(
        self,
        *,
        action: str,
        target: str | None,
        root: Path | None,
        launch_id: str | None,
        mode: RuntimeMode | None,
        config_path: Path | None,
        foreground: bool,
        strategy_ref: str | None = None,
    ) -> dict[str, object] | tuple[object, ...]:
        control = LaunchControl(self.launch_root(root))
        if action == "start":
            if target is not None:
                target_mode, target_config_path = self.launch_target(target)
                mode = mode or target_mode
                config_path = config_path or target_config_path
            if mode is None or config_path is None:
                raise ValueError("daemon start requires TARGET or --mode and --config")
            result = (
                control.launch_foreground(mode=mode, config_path=config_path, launch_id=launch_id, strategy_ref=strategy_ref)
                if foreground
                else control.start_background(mode=mode, config_path=config_path, launch_id=launch_id, strategy_ref=strategy_ref)
            )
            return _daemon_result_payload(result)
        if action == "stop":
            resolved_mode, resolved_launch_id = self.launch_identity(target, mode=mode, launch_id=launch_id, root=root)
            if resolved_mode is None or resolved_launch_id is None:
                raise ValueError("daemon stop requires TARGET or --mode and --launch-id")
            path = control.request_stop(mode=resolved_mode, launch_id=resolved_launch_id, reason="requested by cli")
            return {"command_file": str(path), "mode": resolved_mode.value, "launch_id": resolved_launch_id, "desired_state": "stopped"}
        if action != "status":
            raise ValueError(f"daemon action {action!r} is not supported by the rewritten runtime registry")
        resolved_mode, resolved_launch_id = self.launch_identity(target, mode=mode, launch_id=launch_id, root=root, require_mode=False)
        return control.list(mode=resolved_mode, launch_id=resolved_launch_id)

    def launch_root(self, root: Path | None) -> Path:
        if root is not None:
            return root
        return resolve_workspace().launch_root

    def launch_identity(
        self,
        target: str | None,
        *,
        mode: RuntimeMode | None,
        launch_id: str | None,
        root: Path | None = None,
        require_mode: bool = True,
    ) -> tuple[RuntimeMode | None, str | None]:
        if target is not None:
            try:
                workspace = resolve_workspace()
                config_path = workspace.launch_index.resolve_config_path(target)
                launch_config = load_launch_config(config_path)
                return RuntimeMode(launch_config.mode), launch_config.launch_id
            except Exception:
                if launch_id is None:
                    launch_id = target
        if mode is None and launch_id is not None:
            try:
                mode = _infer_mode_for_launch_id(LaunchControl(self.launch_root(root)), launch_id)
            except ValueError:
                if require_mode:
                    raise
        if require_mode and mode is None:
            raise ValueError("launch command requires --mode when target is not a registered launch/config")
        if require_mode and launch_id is None:
            raise ValueError("launch command requires a launch id when target is not a registered launch/config")
        return mode, launch_id

    def launch_target(self, target: str) -> tuple[RuntimeMode, Path]:
        workspace = resolve_workspace()
        config_path = workspace.launch_index.resolve_config_path(target)
        launch_config = load_launch_config(config_path)
        return RuntimeMode(launch_config.mode), config_path

    def single_launch_record(
        self,
        target: str | None,
        *,
        mode: RuntimeMode | None,
        launch_id: str | None,
        root: Path | None,
    ) -> object:
        records = self.records(target=target, mode=mode, launch_id=launch_id, root=root)
        if not records:
            raise ValueError("launch record was not found")
        if len(records) > 1:
            selected = _select_current_record(records, launch_root=self.launch_root(root))
            if selected is not None:
                return selected
            raise ValueError("multiple launch records matched; pass a registered launch, --mode, or --launch-id")
        return records[0]


def record_payload(record: object) -> Mapping[str, object]:
    method = getattr(record, "to_dict", None)
    if callable(method):
        return method()
    return record if isinstance(record, Mapping) else {"record": record}


def _runtime_target_payloads(launch_root: Path, *, registered_keys: set[tuple[str, str]]) -> tuple[dict[str, object], ...]:
    targets: dict[tuple[str, str], dict[str, object]] = {}
    for record in LaunchControl(launch_root).list():
        item = record_payload(record)
        mode = str(item.get("mode") or "")
        launch_id = str(item.get("launch_id") or "")
        if not mode or not launch_id or (mode, launch_id) in registered_keys:
            continue
        key = (mode, launch_id)
        current = targets.get(key)
        if current is not None and str(current.get("updated_at") or "") >= str(item.get("updated_at") or ""):
            continue
        result = _mapping(item.get("result"))
        context = _mapping(item.get("context"))
        targets[key] = {
            "name": launch_id,
            "source": "runtime",
            "mode": mode,
            "launch_id": launch_id,
            "strategy": context.get("strategy") or result.get("strategy_id") or "",
            "status": item.get("status"),
            "updated_at": item.get("updated_at"),
            "last_instance": item.get("launch_instance_id"),
            "directory": item.get("directory"),
        }
    return tuple(targets[key] for key in sorted(targets))


def _daemon_result_payload(result: object) -> dict[str, object]:
    return {
        "launch_id": getattr(result, "launch_id"),
        "mode": getattr(result, "mode"),
        "launch_instance_id": getattr(result, "launch_instance_id"),
        "phase": getattr(result, "phase"),
        "directory": str(getattr(result, "directory")),
        "state_file": str(getattr(result, "state_path")),
        "run_database": str(getattr(result, "run_path")),
        "result": dict(getattr(result, "result")),
    }


def _management_record_payload(record: object) -> Mapping[str, object]:
    if isinstance(record, Mapping):
        return record
    state = _mapping(getattr(record, "state", {}))
    summary = _mapping(getattr(record, "summary", {}))
    identity = _mapping(state.get("identity"))
    heartbeat_at = getattr(record, "heartbeat_at", None)
    heartbeat = None if heartbeat_at is None else heartbeat_at.isoformat()
    stale = bool(getattr(record, "stale", False))
    phase = str(getattr(record, "phase", None) or state.get("phase") or summary.get("phase") or "stopped")
    status = "stale" if stale else str(state.get("status") or summary.get("status") or phase)
    return {
        "launch_id": getattr(record, "launch_id", state.get("launch_id")),
        "mode": getattr(record, "mode", state.get("mode")),
        "launch_instance_id": getattr(record, "launch_instance_id", None),
        "pid": _optional_int(identity.get("pid")),
        "identity": identity,
        "directory": str(getattr(record, "directory", "")),
        "phase": phase,
        "status": status,
        "desired_state": str(state.get("desired_state") or "stopped"),
        "heartbeat_at": heartbeat,
        "heartbeat_fresh": not stale if phase in {"starting", "running", "stopping"} else None,
        "updated_at": getattr(record, "updated_at", None).isoformat() if getattr(record, "updated_at", None) is not None else None,
        "heartbeat_age_seconds": getattr(record, "heartbeat_age_seconds", None),
        "stale": stale,
        "stale_reason": _stale_reason(record, stale=stale),
        "log_file": str(getattr(record, "directory", Path("")) / "launch.log") if (getattr(record, "directory", Path("")) / "launch.log").exists() else None,
        "context": {
            **dict(state.get("context", {}) if isinstance(state.get("context"), Mapping) else {}),
            "strategy": str(summary.get("strategy_id") or ""),
        },
        "result": dict(summary),
    }


def _stale_reason(record: object, *, stale: bool) -> str | None:
    if not stale:
        return None
    return "heartbeat_missing" if getattr(record, "heartbeat_at", None) is None else "heartbeat_expired"


def _log_path(record: object) -> Path | None:
    directory = getattr(record, "directory")
    return _first_log_file((directory / "daemon.log", directory / "launch.log"))


def _select_current_record(records: tuple[object, ...], *, launch_root: Path) -> object | None:
    by_directory = {str(getattr(record, "directory", "")): record for record in records}
    for record in records:
        mode = str(getattr(record, "mode", "") or "")
        launch_id = str(getattr(record, "launch_id", "") or "")
        if not mode or not launch_id:
            continue
        current = _read_json(launch_root / mode / launch_id / "current.json")
        directory = _current_launch_directory(launch_root, mode=mode, launch_id=launch_id, current=current)
        if directory is not None and str(directory) in by_directory:
            return by_directory[str(directory)]
    return None


def _current_launch_directory(launch_root: Path, *, mode: str, launch_id: str, current: Mapping[str, object]) -> Path | None:
    directory = current.get("directory")
    if isinstance(directory, str) and directory.strip():
        path = Path(directory)
        return path if path.is_absolute() else launch_root / mode / launch_id / path
    instance_id = current.get("launch_instance_id")
    if isinstance(instance_id, str) and instance_id.strip():
        return launch_root / mode / launch_id / "instances" / instance_id
    return None


def _first_log_file(candidates: tuple[Path, ...]) -> Path | None:
    existing = [candidate for candidate in candidates if candidate.exists()]
    for candidate in existing:
        if candidate.stat().st_size > 0:
            return candidate
    return existing[0] if existing else None


def _wait_for_command_response(path: Path, *, timeout_seconds: float) -> Mapping[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, Mapping) else {}
        time.sleep(0.05)
    raise ValueError(f"system command response timed out: {path}")


def _wait_for_launch_inactive(control: LaunchControl, *, mode: RuntimeMode, launch_id: str, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not any(_launch_record_active(record) for record in control.list(mode=mode, launch_id=launch_id)):
            return
        time.sleep(0.05)
    raise ValueError(f"launch did not stop within {timeout_seconds:g}s: {mode.value}:{launch_id}")


def _launch_record_active(record: object) -> bool:
    return getattr(record, "phase", None) in {"starting", "running", "stopping"} and not bool(getattr(record, "stale", True))


def _infer_mode_for_launch_id(control: LaunchControl, launch_id: str) -> RuntimeMode:
    records = control.list(launch_id=launch_id)
    modes = sorted({str(getattr(record, "mode", "") or "") for record in records if str(getattr(record, "mode", "") or "")})
    if not modes:
        raise ValueError(f"launch record was not found: {launch_id}; pass a registered launch/config or --mode")
    if len(modes) > 1:
        raise ValueError(f"multiple launch modes matched for launch id {launch_id}; pass --mode")
    return RuntimeMode(modes[0])


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _current_directory(launch_root: Path, launch_id: str, current: Mapping[str, object]) -> Path | None:
    directory = current.get("directory")
    if isinstance(directory, str) and directory.strip():
        path = Path(directory)
        return path if path.is_absolute() else launch_root / RuntimeMode.SYSTEM.value / launch_id / path
    instance_id = current.get("launch_instance_id")
    if isinstance(instance_id, str) and instance_id.strip():
        return launch_root / RuntimeMode.SYSTEM.value / launch_id / "instances" / instance_id
    return None


def _record_for_directory(records: tuple[Mapping[str, object], ...], directory: Path | None) -> Mapping[str, object] | None:
    if directory is None:
        return None
    directory_text = str(directory)
    for record in records:
        if str(record.get("directory") or "") == directory_text:
            return record
    return None


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


def _matching_system_processes(*, launch_root: Path, launch_id: str) -> tuple[dict[str, object], ...]:
    try:
        result = subprocess.run(["ps", "-axo", "pid=,command="], check=False, capture_output=True, text=True)
    except OSError:
        return ()
    if result.returncode != 0:
        return ()
    current_pid = os.getpid()
    root_text = str(launch_root)
    processes: list[dict[str, object]] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        pid = _optional_int(pid_text)
        if pid is None or pid == current_pid:
            continue
        argv = _split_command(command)
        if _matches_system_process(argv, command=command, launch_root=root_text, launch_id=launch_id):
            processes.append({"pid": pid, "command": command, "argv": argv})
    return tuple(processes)


def _split_command(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _matches_system_process(argv: list[str], *, command: str, launch_root: str, launch_id: str) -> bool:
    if not _contains_sequence(argv, ("-m", "kairospy", "system", "up")) and "kairospy system up" not in command:
        return False
    if "--foreground" not in argv:
        return False
    if not _option_matches(argv, "--root", launch_root):
        return False
    return _option_matches(argv, "--launch-id", launch_id)


def _contains_sequence(values: list[str], sequence: tuple[str, ...]) -> bool:
    width = len(sequence)
    return any(tuple(values[index : index + width]) == sequence for index in range(0, len(values) - width + 1))


def _option_matches(argv: list[str], option: str, expected: str) -> bool:
    for index, value in enumerate(argv):
        if value == option and index + 1 < len(argv):
            return argv[index + 1] == expected
        prefix = f"{option}="
        if value.startswith(prefix):
            return value[len(prefix) :] == expected
    return False


def _system_health(
    *,
    current_record: Mapping[str, object] | None,
    pid: int | None,
    pid_alive: bool | None,
    processes: tuple[Mapping[str, object], ...],
    orphaned_processes: tuple[Mapping[str, object], ...],
) -> str:
    if len(processes) > 1:
        return "conflicted"
    if current_record is None:
        return "orphaned" if orphaned_processes else "unknown"
    phase = str(current_record.get("phase") or "")
    active = phase in {"starting", "running", "stopping"}
    stale = bool(current_record.get("stale"))
    if orphaned_processes:
        return "conflicted"
    if active and stale:
        return "stale" if pid_alive else "dead"
    if active and pid is not None and pid_alive is False:
        return "dead"
    if active and (pid is None or pid_alive is None):
        return "unknown"
    if active:
        return "healthy"
    return "unknown" if processes else str(current_record.get("status") or phase or "unknown")


def _clean_current_stale_system(inspect: Mapping[str, object], *, timeout_seconds: float) -> Mapping[str, object] | None:
    if inspect.get("health") != "stale" or inspect.get("pid_alive") is not True:
        return None
    pid = _optional_int(inspect.get("pid"))
    processes = _mapping(inspect.get("processes"))
    managed = processes.get("managed")
    if pid is None or not isinstance(managed, list) or not any(isinstance(item, Mapping) and item.get("pid") == pid for item in managed):
        return None
    os.kill(pid, signal.SIGTERM)
    exited = _wait_for_pid_exit(pid, timeout_seconds=timeout_seconds)
    return {"pid": pid, "signal": "SIGTERM", "exited": exited}


def _wait_for_pid_exit(pid: int, *, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.05)
    return not _pid_alive(pid)


__all__ = ["DEFAULT_SYSTEM_LAUNCH_ID", "LaunchAlreadyActiveError", "LaunchApplication", "RuntimeMode", "TradingConfigurationError", "record_payload"]
