from __future__ import annotations

from pathlib import Path
from typing import Mapping
import json
import time

from kairospy.application.launch.control import LaunchControl
from kairospy.application.launch.daemon import LaunchAlreadyActiveError
from kairospy.application.launch.launcher import TradingConfigurationError, TradingSystemLauncher
from kairospy.application.modes import RuntimeMode
from kairospy.application.system.facade.context import workspace as resolve_workspace
from kairospy.application.launch.host.runtime_host import TradingSystemSession
from kairospy.config import SYSTEM_LAUNCH_ID, load_launch_config


DEFAULT_SYSTEM_LAUNCH_ID = SYSTEM_LAUNCH_ID


class LaunchFacade:
    def __init__(self, launcher: TradingSystemLauncher | None = None) -> None:
        self._launcher = launcher or TradingSystemLauncher()

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
        for entry in workspace.launch_index.list():
            payload: dict[str, object] = {
                "name": entry.name,
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
                account_source = str(workspace.accounts.get(account_ref).source_path)
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
        result = LaunchControl(self.launch_root(None)).launch_foreground(mode=mode, config_path=path, strategy_ref=strategy_ref)
        return _daemon_result_payload(result)

    def stop(self, *, target: str | None, mode: RuntimeMode | None, launch_id: str | None, root: Path | None) -> dict[str, object]:
        resolved_mode, resolved_launch_id = self.launch_identity(target, mode=mode, launch_id=launch_id)
        path = LaunchControl(self.launch_root(root)).request_stop(mode=resolved_mode, launch_id=resolved_launch_id, reason="requested by cli")
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
        resolved_mode, resolved_launch_id = self.launch_identity(target, mode=mode, launch_id=launch_id)
        result = LaunchControl(self.launch_root(root)).submit_command(
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

    def records(self, *, target: str | None = None, mode: RuntimeMode | None = None, launch_id: str | None = None, root: Path | None = None) -> tuple[object, ...]:
        resolved_mode, resolved_launch_id = self.launch_identity(target, mode=mode, launch_id=launch_id, require_mode=False)
        return LaunchControl(self.launch_root(root)).list(mode=resolved_mode, launch_id=resolved_launch_id)

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
        return self._launcher.launch_events(strategy_path=strategy_path, events_path=events_path, launch_id=launch_id, mode=mode)

    def open_system_session(self, *, strategy_path: str, launch_id: str, mode: RuntimeMode) -> TradingSystemSession:
        return self._launcher.open_system_session(strategy_path=strategy_path, launch_id=launch_id, mode=mode)

    def system_up(
        self,
        *,
        root: Path | None = None,
        launch_id: str = DEFAULT_SYSTEM_LAUNCH_ID,
        foreground: bool = False,
    ) -> dict[str, object]:
        control = LaunchControl(self.launch_root(root))
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
    ) -> dict[str, object]:
        launch_root = self.launch_root(root)
        control = LaunchControl(launch_root)
        stop: Mapping[str, object] | None = None
        if any(_launch_record_active(record) for record in control.list(mode=RuntimeMode.SYSTEM, launch_id=launch_id)):
            path = control.request_stop(
                mode=RuntimeMode.SYSTEM,
                launch_id=launch_id,
                reason="requested by system restart",
            )
            stop = {"command_file": str(path), "mode": RuntimeMode.SYSTEM.value, "launch_id": launch_id, "desired_state": "stopped"}
            _wait_for_launch_inactive(control, mode=RuntimeMode.SYSTEM, launch_id=launch_id, timeout_seconds=timeout_seconds)
        start = (
            control.launch_system_foreground(launch_id=launch_id)
            if foreground
            else control.start_system_background(launch_id=launch_id)
        )
        return {"action": "restart", "stopped": stop, "started": _daemon_result_payload(start)}

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

    def system_log_file(self, *, root: Path | None = None, launch_id: str = DEFAULT_SYSTEM_LAUNCH_ID) -> Path:
        path = _log_path(self.single_launch_record(None, mode=RuntimeMode.SYSTEM, launch_id=launch_id, root=root))
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
            resolved_mode, resolved_launch_id = self.launch_identity(target, mode=mode, launch_id=launch_id)
            if resolved_mode is None or resolved_launch_id is None:
                raise ValueError("daemon stop requires TARGET or --mode and --launch-id")
            path = control.request_stop(mode=resolved_mode, launch_id=resolved_launch_id, reason="requested by cli")
            return {"command_file": str(path), "mode": resolved_mode.value, "launch_id": resolved_launch_id, "desired_state": "stopped"}
        if action != "status":
            raise ValueError(f"daemon action {action!r} is not supported by the rewritten runtime registry")
        resolved_mode, resolved_launch_id = self.launch_identity(target, mode=mode, launch_id=launch_id, require_mode=False)
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
            raise ValueError("multiple launch records matched; pass a registered launch, --mode, or --launch-id")
        return records[0]


def record_payload(record: object) -> Mapping[str, object]:
    method = getattr(record, "to_dict", None)
    if callable(method):
        return method()
    return record if isinstance(record, Mapping) else {"record": record}


def _daemon_result_payload(result: object) -> dict[str, object]:
    return {
        "launch_id": getattr(result, "launch_id"),
        "mode": getattr(result, "mode"),
        "launch_instance_id": getattr(result, "launch_instance_id"),
        "phase": getattr(result, "phase"),
        "directory": str(getattr(result, "directory")),
        "state_file": str(getattr(result, "state_path")),
        "summary_file": str(getattr(result, "summary_path")),
        "result": dict(getattr(result, "result")),
    }


def _log_path(record: object) -> Path | None:
    directory = getattr(record, "directory")
    return _first_log_file((directory / "daemon.log", directory / "launch.log", directory / "events.jsonl"))


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


__all__ = ["DEFAULT_SYSTEM_LAUNCH_ID", "LaunchAlreadyActiveError", "LaunchFacade", "RuntimeMode", "TradingConfigurationError", "record_payload"]
