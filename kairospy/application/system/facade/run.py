from __future__ import annotations

from pathlib import Path
from typing import Mapping

from kairospy.application.system import RunAlreadyActiveError, RunControl, TradingConfigurationError, TradingSystemLauncher
from kairospy.application.system.facade.run_control import RuntimeMode
from kairospy.application.system.workspace import KairosWorkspace
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
        workspace = KairosWorkspace.resolve()
        entry = workspace.run_index.register(name, config_path)
        workspace.operations.append("run.register", target={"run": entry.name}, payload={"config": entry.config_path, "index": workspace.run_index.path})
        return {"name": entry.name, "config": str(entry.config_path), "index": str(workspace.run_index.path)}

    def unregister(self, name: str) -> dict[str, object]:
        workspace = KairosWorkspace.resolve()
        entry = workspace.run_index.unregister(name)
        workspace.operations.append("run.unregister", target={"run": entry.name}, payload={"config": entry.config_path, "index": workspace.run_index.path})
        return {"name": entry.name, "config": str(entry.config_path), "index": str(workspace.run_index.path), "removed": True}

    def specs(self) -> dict[str, object]:
        return KairosWorkspace.resolve().run_index.to_dict()

    def validate(self, target: str) -> dict[str, object]:
        workspace = KairosWorkspace.resolve()
        path = workspace.run_index.resolve_config_path(target)
        report = load_run_config(path).validation_report()
        return {"target": target, "path": str(report.path), "valid": report.valid, "issues": list(report.issues)}

    def explain(self, target: str) -> dict[str, object]:
        workspace = KairosWorkspace.resolve()
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

    def start(self, target: str) -> object:
        workspace = KairosWorkspace.resolve()
        path = workspace.run_index.resolve_config_path(target)
        run_config = load_run_config(path)
        mode = RuntimeMode(run_config.mode)
        if mode is RuntimeMode.BACKTEST:
            return self._launcher.run_backtest_config(path)
        if mode is RuntimeMode.PAPER:
            return self._launcher.run_paper_config(path)
        return self._launcher.run_live_config(path)

    def stop(self, *, target: str | None, mode: RuntimeMode | None, run_id: str | None, root: Path | None) -> dict[str, object]:
        resolved_mode, resolved_run_id = self.run_identity(target, mode=mode, run_id=run_id)
        path = RunControl(self.run_root(root)).request_stop(mode=resolved_mode, run_id=resolved_run_id, reason="requested by cli")
        KairosWorkspace.resolve().operations.append(
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
        candidates = (record.directory / "daemon.log", record.directory / "run.log", record.directory / "events.jsonl")
        path = _first_log_file(candidates)
        if path is None:
            return {"run": record_payload(record), "log_file": None, "lines": []}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return {"run": record_payload(record), "log_file": str(path), "lines": lines[-limit:]}

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

    def run_backtest_config(self, config_path: Path) -> object:
        return self._launcher.run_backtest_config(config_path)

    def run_paper_config(self, config_path: Path) -> object:
        return self._launcher.run_paper_config(config_path)

    def run_live_config(self, config_path: Path) -> object:
        return self._launcher.run_live_config(config_path)

    def daemon(
        self,
        *,
        action: str,
        root: Path | None,
        run_id: str | None,
        mode: RuntimeMode | None,
        config_path: Path | None,
        foreground: bool,
    ) -> dict[str, object] | tuple[object, ...]:
        control = RunControl(self.run_root(root))
        if action == "start":
            if mode is None or config_path is None:
                raise ValueError("daemon start requires --mode and --config")
            result = (
                control.run_foreground(mode=mode, config_path=config_path, run_id=run_id)
                if foreground
                else control.start_background(mode=mode, config_path=config_path, run_id=run_id)
            )
            return {
                "run_id": result.run_id,
                "mode": result.mode,
                "run_instance_id": result.run_instance_id,
                "phase": result.phase,
                "directory": str(result.directory),
                "state_file": str(result.state_path),
                "summary_file": str(result.summary_path),
                "result": dict(result.result),
            }
        if action == "stop":
            if mode is None or run_id is None:
                raise ValueError("daemon stop requires --mode and --run-id")
            path = control.request_stop(mode=mode, run_id=run_id, reason="requested by cli")
            return {"command_file": str(path), "mode": mode.value, "run_id": run_id, "desired_state": "stopped"}
        if action != "status":
            raise ValueError(f"daemon action {action!r} is not supported by the rewritten runtime registry")
        return control.list(mode=mode, run_id=run_id)

    def run_root(self, root: Path | None) -> Path:
        if root is not None:
            return root
        return KairosWorkspace.resolve().run_root

    def run_identity(
        self,
        target: str | None,
        *,
        mode: RuntimeMode | None,
        run_id: str | None,
        require_mode: bool = True,
    ) -> tuple[RuntimeMode | None, str | None]:
        if target is not None:
            workspace = KairosWorkspace.resolve()
            try:
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


def _first_log_file(candidates: tuple[Path, ...]) -> Path | None:
    existing = [candidate for candidate in candidates if candidate.exists()]
    for candidate in existing:
        if candidate.stat().st_size > 0:
            return candidate
    return existing[0] if existing else None


__all__ = ["RunAlreadyActiveError", "RunFacade", "RuntimeMode", "TradingConfigurationError", "record_payload"]
