from __future__ import annotations

from pathlib import Path
from typing import Mapping

from kairospy.application.system import RunAlreadyActiveError, RunControl, TradingConfigurationError, TradingSystemLauncher
from kairospy.application.system.facade.run_control import RuntimeMode
from kairospy.application.system.facade.context import workspace as resolve_workspace
from kairospy.config import load_run_config


class RunFacade:
    def __init__(self, launcher: TradingSystemLauncher | None = None) -> None:
        self._launcher = launcher or TradingSystemLauncher()

    def config(self, *, action: str, path: Path) -> dict[str, object]:
        run_config = load_run_config(path)
        if action == "validate":
            report = run_config.validation_report()
            return {"path": str(report.path), "valid": report.valid, "issues": list(report.issues)}
        if action == "explain":
            return run_config.explain()
        raise ValueError(f"unsupported config action: {action}")

    def register(self, *, name: str, config_path: Path) -> dict[str, object]:
        workspace = resolve_workspace()
        entry = workspace.run_index.register(name, config_path)
        workspace.operations.append("run.register", target={"run": entry.name}, payload={"config": entry.config_path, "index": workspace.run_index.path})
        return {"name": entry.name, "config": str(entry.config_path), "index": str(workspace.run_index.path)}

    def register_target(self, *, name_or_config_path: str, config_path: Path | None) -> dict[str, object]:
        if config_path is not None:
            return self.register(name=name_or_config_path, config_path=config_path)
        path = Path(name_or_config_path)
        return self.register(name=load_run_config(path).run_id, config_path=path)

    def unregister(self, name: str) -> dict[str, object]:
        workspace = resolve_workspace()
        entry = workspace.run_index.unregister(name)
        workspace.operations.append("run.unregister", target={"run": entry.name}, payload={"config": entry.config_path, "index": workspace.run_index.path})
        return {"name": entry.name, "config": str(entry.config_path), "index": str(workspace.run_index.path), "removed": True}

    def specs(self) -> dict[str, object]:
        return resolve_workspace().run_index.to_dict()

    def list(self) -> dict[str, object]:
        workspace = resolve_workspace()
        runs = []
        for entry in workspace.run_index.list():
            payload: dict[str, object] = {
                "name": entry.name,
                "config": str(entry.config_path),
                "registered_at": entry.registered_at,
                "last_instance": entry.last_instance,
            }
            try:
                run_config = load_run_config(entry.config_path)
            except Exception as error:
                payload["valid"] = False
                payload["error"] = str(error)
            else:
                payload.update(
                    {
                        "valid": True,
                        "mode": run_config.mode,
                        "run_id": run_config.run_id,
                        "strategy": run_config.strategy,
                    }
                )
            runs.append(payload)
        return {"runs": runs, "count": len(runs), "path": str(workspace.run_index.path)}

    def validate(self, target: str) -> dict[str, object]:
        workspace = resolve_workspace()
        path = workspace.run_index.resolve_config_path(target)
        report = load_run_config(path).validation_report()
        return {"target": target, "path": str(report.path), "valid": report.valid, "issues": list(report.issues)}

    def explain(self, target: str) -> dict[str, object]:
        workspace = resolve_workspace()
        path = workspace.run_index.resolve_config_path(target)
        run_config = load_run_config(path)
        account_ref = run_config.account_ref
        account_source = None
        if account_ref:
            try:
                account_source = str(workspace.accounts.get(account_ref).source_path)
            except Exception:
                account_source = None
        return {
            "target": target,
            "path": str(path),
            "run_config": run_config.explain(),
            "mode": run_config.mode,
            "run_id": run_config.run_id,
            "strategy": run_config.strategy,
            "account_ref": account_ref,
            "sources": {
                "run_config": str(run_config.path) if run_config.path is not None else None,
                "workspace_manifest": str(workspace.manifest_path) if workspace.manifest_path is not None else None,
                "account": account_source,
            },
        }

    def start(self, target: str, *, strategy_ref: str | None = None) -> object:
        mode, path = self.run_target(target)
        result = RunControl(self.run_root(None)).run_foreground(mode=mode, config_path=path, strategy_ref=strategy_ref)
        return _daemon_result_payload(result)

    def stop(self, *, target: str | None, mode: RuntimeMode | None, run_id: str | None, root: Path | None) -> dict[str, object]:
        resolved_mode, resolved_run_id = self.run_identity(target, mode=mode, run_id=run_id)
        path = RunControl(self.run_root(root)).request_stop(mode=resolved_mode, run_id=resolved_run_id, reason="requested by cli")
        resolve_workspace().operations.append(
            "run.stop",
            target={"mode": resolved_mode.value, "run_id": resolved_run_id},
            payload={"command_file": path},
        )
        return {"command_file": str(path), "mode": resolved_mode.value, "run_id": resolved_run_id, "desired_state": "stopped"}

    def records(self, *, target: str | None = None, mode: RuntimeMode | None = None, run_id: str | None = None, root: Path | None = None) -> tuple[object, ...]:
        resolved_mode, resolved_run_id = self.run_identity(target, mode=mode, run_id=run_id, require_mode=False)
        return RunControl(self.run_root(root)).list(mode=resolved_mode, run_id=resolved_run_id)

    def logs(
        self,
        *,
        target: str | None,
        mode: RuntimeMode | None,
        run_id: str | None,
        root: Path | None,
        limit: int,
    ) -> dict[str, object]:
        record = self.single_run_record(target, mode=mode, run_id=run_id, root=root)
        path = _log_path(record)
        if path is None:
            return {"run": record_payload(record), "log_file": None, "lines": []}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return {"run": record_payload(record), "log_file": str(path), "lines": lines[-limit:]}

    def log_path(
        self,
        *,
        target: str | None,
        mode: RuntimeMode | None,
        run_id: str | None,
        root: Path | None,
    ) -> Path | None:
        return _log_path(self.single_run_record(target, mode=mode, run_id=run_id, root=root))

    def log_file(
        self,
        *,
        target: str | None,
        mode: RuntimeMode | None,
        run_id: str | None,
        root: Path | None,
    ) -> Path:
        path = self.log_path(target=target, mode=mode, run_id=run_id, root=root)
        if path is None:
            raise ValueError("run log was not found")
        return path

    def artifacts(
        self,
        *,
        target: str | None,
        mode: RuntimeMode | None,
        run_id: str | None,
        root: Path | None,
    ) -> dict[str, object]:
        record = self.single_run_record(target, mode=mode, run_id=run_id, root=root)
        files = [
            {"path": str(path), "name": path.name, "size": path.stat().st_size}
            for path in sorted(record.directory.rglob("*"))
            if path.is_file()
        ]
        return {"run": record_payload(record), "directory": str(record.directory), "artifacts": files, "count": len(files)}

    def run_events(self, *, strategy_path: str, events_path: Path, run_id: str, mode: RuntimeMode) -> object:
        return self._launcher.run_events(strategy_path=strategy_path, events_path=events_path, run_id=run_id, mode=mode)

    def daemon(
        self,
        *,
        action: str,
        target: str | None,
        root: Path | None,
        run_id: str | None,
        mode: RuntimeMode | None,
        config_path: Path | None,
        foreground: bool,
        strategy_ref: str | None = None,
    ) -> dict[str, object] | tuple[object, ...]:
        control = RunControl(self.run_root(root))
        if action == "start":
            if target is not None:
                target_mode, target_config_path = self.run_target(target)
                mode = mode or target_mode
                config_path = config_path or target_config_path
            if mode is None or config_path is None:
                raise ValueError("daemon start requires TARGET or --mode and --config")
            result = (
                control.run_foreground(mode=mode, config_path=config_path, run_id=run_id, strategy_ref=strategy_ref)
                if foreground
                else control.start_background(mode=mode, config_path=config_path, run_id=run_id, strategy_ref=strategy_ref)
            )
            return _daemon_result_payload(result)
        if action == "stop":
            resolved_mode, resolved_run_id = self.run_identity(target, mode=mode, run_id=run_id)
            if resolved_mode is None or resolved_run_id is None:
                raise ValueError("daemon stop requires TARGET or --mode and --run-id")
            path = control.request_stop(mode=resolved_mode, run_id=resolved_run_id, reason="requested by cli")
            return {"command_file": str(path), "mode": resolved_mode.value, "run_id": resolved_run_id, "desired_state": "stopped"}
        if action != "status":
            raise ValueError(f"daemon action {action!r} is not supported by the rewritten runtime registry")
        resolved_mode, resolved_run_id = self.run_identity(target, mode=mode, run_id=run_id, require_mode=False)
        return control.list(mode=resolved_mode, run_id=resolved_run_id)

    def run_root(self, root: Path | None) -> Path:
        if root is not None:
            return root
        return resolve_workspace().run_root

    def run_identity(
        self,
        target: str | None,
        *,
        mode: RuntimeMode | None,
        run_id: str | None,
        require_mode: bool = True,
    ) -> tuple[RuntimeMode | None, str | None]:
        if target is not None:
            try:
                workspace = resolve_workspace()
                config_path = workspace.run_index.resolve_config_path(target)
                run_config = load_run_config(config_path)
                return RuntimeMode(run_config.mode), run_config.run_id
            except Exception:
                if run_id is None:
                    run_id = target
        if require_mode and mode is None:
            raise ValueError("run command requires --mode when target is not a registered run/config")
        if require_mode and run_id is None:
            raise ValueError("run command requires a run id when target is not a registered run/config")
        return mode, run_id

    def run_target(self, target: str) -> tuple[RuntimeMode, Path]:
        workspace = resolve_workspace()
        config_path = workspace.run_index.resolve_config_path(target)
        run_config = load_run_config(config_path)
        return RuntimeMode(run_config.mode), config_path

    def single_run_record(
        self,
        target: str | None,
        *,
        mode: RuntimeMode | None,
        run_id: str | None,
        root: Path | None,
    ) -> object:
        records = self.records(target=target, mode=mode, run_id=run_id, root=root)
        if not records:
            raise ValueError("run record was not found")
        if len(records) > 1:
            raise ValueError("multiple run records matched; pass a registered run, --mode, or --run-id")
        return records[0]


def record_payload(record: object) -> Mapping[str, object]:
    method = getattr(record, "to_dict", None)
    if callable(method):
        return method()
    return record if isinstance(record, Mapping) else {"record": record}


def _daemon_result_payload(result: object) -> dict[str, object]:
    return {
        "run_id": getattr(result, "run_id"),
        "mode": getattr(result, "mode"),
        "run_instance_id": getattr(result, "run_instance_id"),
        "phase": getattr(result, "phase"),
        "directory": str(getattr(result, "directory")),
        "state_file": str(getattr(result, "state_path")),
        "summary_file": str(getattr(result, "summary_path")),
        "result": dict(getattr(result, "result")),
    }


def _log_path(record: object) -> Path | None:
    directory = getattr(record, "directory")
    return _first_log_file((directory / "daemon.log", directory / "run.log", directory / "events.jsonl"))


def _first_log_file(candidates: tuple[Path, ...]) -> Path | None:
    existing = [candidate for candidate in candidates if candidate.exists()]
    for candidate in existing:
        if candidate.stat().st_size > 0:
            return candidate
    return existing[0] if existing else None


__all__ = ["RunAlreadyActiveError", "RunFacade", "RuntimeMode", "TradingConfigurationError", "record_payload"]
