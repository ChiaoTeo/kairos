from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import StrEnum
import json
import shlex
import sys
from pathlib import Path
from typing import Mapping, TextIO

import typer

from kairospy.application.runtime import RuntimeMode
from kairospy.application.system import RunAlreadyActiveError, RunControl, TradingConfigurationError, TradingSystemLauncher
from kairospy.application.system.workspace import KairosWorkspace
from kairospy.config import load_run_config


run_app = typer.Typer(no_args_is_help=True, help="Run commands")
_TRADING_LAUNCHER = TradingSystemLauncher()


class OutputFormat(StrEnum):
    auto = "auto"
    json = "json"
    text = "text"


@run_app.command("config")
def config(
    action: str = typer.Argument(...),
    path: Path = typer.Argument(...),
) -> None:
    run_config = load_run_config(path)
    if action == "validate":
        report = run_config.validation_report()
        _echo({
            "path": str(report.path),
            "valid": report.valid,
            "issues": list(report.issues),
        })
        if not report.valid:
            raise typer.Exit(2)
        return
    if action == "explain":
        _echo(run_config.explain())
        return
    raise typer.BadParameter(f"unsupported config action: {action}")


@run_app.command("register")
def register(
    name: str = typer.Argument(...),
    config_path: Path = typer.Argument(...),
) -> None:
    workspace = KairosWorkspace.resolve()
    entry = workspace.run_index.register(name, config_path)
    workspace.operations.append("run.register", target={"run": entry.name}, payload={"config": entry.config_path, "index": workspace.run_index.path})
    _echo({"name": entry.name, "config": str(entry.config_path), "index": str(workspace.run_index.path)})


@run_app.command("unregister")
def unregister(name: str = typer.Argument(...)) -> None:
    workspace = KairosWorkspace.resolve()
    entry = workspace.run_index.unregister(name)
    workspace.operations.append("run.unregister", target={"run": entry.name}, payload={"config": entry.config_path, "index": workspace.run_index.path})
    _echo({"name": entry.name, "config": str(entry.config_path), "index": str(workspace.run_index.path), "removed": True})


@run_app.command("specs")
def specs(output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format")) -> None:
    workspace = KairosWorkspace.resolve()
    payload = workspace.run_index.to_dict()
    if _use_json_output(output_format):
        _echo(payload)
        return
    entries = workspace.run_index.list()
    if not entries:
        typer.echo(f"Run Specs\n  none\n  index {workspace.run_index.path}")
        return
    lines = ["Run Specs"]
    for entry in entries:
        lines.append(f"  {entry.name}  {entry.config_path}")
    typer.echo("\n".join(lines))


@run_app.command("validate")
def validate(
    target: str = typer.Argument(..., help="Registered run name or run config path"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
) -> None:
    workspace = KairosWorkspace.resolve()
    path = workspace.run_index.resolve_config_path(target)
    run_config = load_run_config(path)
    report = run_config.validation_report()
    payload = {
        "target": target,
        "path": str(report.path),
        "valid": report.valid,
        "issues": list(report.issues),
    }
    _echo(payload) if _use_json_output(output_format) else _echo_validation_text(payload)
    if not report.valid:
        raise typer.Exit(2)


@run_app.command("explain")
def explain(
    target: str = typer.Argument(..., help="Registered run name or run config path"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
) -> None:
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
    payload = {
        "target": target,
        "path": str(path),
        "run_config": run_config.explain(),
        "sources": {
            "run_config": str(run_config.path) if run_config.path is not None else None,
            "workspace_manifest": str(workspace.manifest_path) if workspace.manifest_path is not None else None,
            "account": account_source,
        },
    }
    if _use_json_output(output_format):
        _echo(payload)
        return
    lines = [
        f"Run Config {target}",
        f"  path       {path}",
        f"  mode       {run_config.mode}",
        f"  run_id     {run_config.run_id}",
        f"  strategy   {run_config.strategy or ''}",
        f"  account    {account_ref or ''}",
        f"  source     {account_source or ''}",
    ]
    typer.echo("\n".join(lines))


@run_app.command("start")
def start(
    target: str = typer.Argument(..., help="Registered run name or run config path"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
) -> None:
    workspace = KairosWorkspace.resolve()
    path = workspace.run_index.resolve_config_path(target)
    try:
        run_config = load_run_config(path)
        mode = RuntimeMode(run_config.mode)
        if mode is RuntimeMode.BACKTEST:
            result = _TRADING_LAUNCHER.run_backtest_config(path)
        elif mode is RuntimeMode.PAPER:
            result = _TRADING_LAUNCHER.run_paper_config(path)
        else:
            result = _TRADING_LAUNCHER.run_live_config(path)
    except (TradingConfigurationError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    _echo_run_result(result, output_format=output_format)


@run_app.command("stop")
def stop(
    target: str | None = typer.Argument(None, help="Registered run name or run id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    run_id: str | None = typer.Option(None, "--run-id"),
    root: Path | None = typer.Option(None, "--root"),
) -> None:
    resolved_mode, resolved_run_id = _run_identity(target, mode=mode, run_id=run_id)
    path = RunControl(_run_root(root)).request_stop(mode=resolved_mode, run_id=resolved_run_id, reason="requested by cli")
    KairosWorkspace.resolve().operations.append(
        "run.stop",
        target={"mode": resolved_mode.value, "run_id": resolved_run_id},
        payload={"command_file": path},
    )
    _echo({"command_file": str(path), "mode": resolved_mode.value, "run_id": resolved_run_id, "desired_state": "stopped"})


@run_app.command("status")
def status(
    target: str | None = typer.Argument(None, help="Registered run name or run id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    run_id: str | None = typer.Option(None, "--run-id"),
    root: Path | None = typer.Option(None, "--root"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
) -> None:
    resolved_mode, resolved_run_id = _run_identity(target, mode=mode, run_id=run_id, require_mode=False)
    records = RunControl(_run_root(root)).list(mode=resolved_mode, run_id=resolved_run_id)
    _echo_registry(records, output_format=output_format)


@run_app.command("logs")
def logs(
    target: str | None = typer.Argument(None, help="Registered run name or run id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    run_id: str | None = typer.Option(None, "--run-id"),
    root: Path | None = typer.Option(None, "--root"),
    limit: int = typer.Option(100, "--limit"),
    output_format: OutputFormat = typer.Option(OutputFormat.text, "--format"),
) -> None:
    record = _single_run_record(target, mode=mode, run_id=run_id, root=root)
    candidates = (record.directory / "daemon.log", record.directory / "run.log", record.directory / "events.jsonl")
    path = _first_log_file(candidates)
    if path is None:
        payload = {"run": _record_payload(record), "log_file": None, "lines": []}
    else:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        payload = {"run": _record_payload(record), "log_file": str(path), "lines": lines[-limit:]}
    if _use_json_output(output_format):
        _echo(payload)
        return
    if payload["log_file"] is None:
        typer.echo(f"Run Logs\n  none\n  directory {record.directory}")
        return
    typer.echo("\n".join(str(line) for line in payload["lines"]))


@run_app.command("artifacts")
def artifacts(
    target: str | None = typer.Argument(None, help="Registered run name or run id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    run_id: str | None = typer.Option(None, "--run-id"),
    root: Path | None = typer.Option(None, "--root"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
) -> None:
    record = _single_run_record(target, mode=mode, run_id=run_id, root=root)
    files = [
        {
            "path": str(path),
            "name": path.name,
            "size": path.stat().st_size,
        }
        for path in sorted(record.directory.rglob("*"))
        if path.is_file()
    ]
    payload = {"run": _record_payload(record), "directory": str(record.directory), "artifacts": files, "count": len(files)}
    if _use_json_output(output_format):
        _echo(payload)
        return
    if not files:
        typer.echo(f"Run Artifacts\n  none\n  directory {record.directory}")
        return
    lines = ["Run Artifacts", f"  directory {record.directory}"]
    lines.extend(f"  {item['size']:>8}  {item['path']}" for item in files)
    typer.echo("\n".join(lines))


@run_app.command("events")
def events(
    strategy_path: str = typer.Option(..., "--strategy", help="Strategy import path: module:callable"),
    events_path: Path = typer.Option(..., "--events", help="JSONL RuntimeEnvelope file"),
    run_id: str = typer.Option("kairos-run", "--run-id"),
    mode: RuntimeMode = typer.Option(RuntimeMode.BACKTEST, "--mode"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
) -> None:
    try:
        result = _TRADING_LAUNCHER.run_events(strategy_path=strategy_path, events_path=events_path, run_id=run_id, mode=mode)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _echo_run_result(result, output_format=output_format)


@run_app.command("backtest")
def backtest(
    config_path: Path = typer.Option(..., "--config"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
) -> None:
    try:
        result = _TRADING_LAUNCHER.run_backtest_config(config_path)
    except TradingConfigurationError as error:
        raise typer.BadParameter(str(error)) from error
    _echo_run_result(result, output_format=output_format)


@run_app.command("paper")
def paper(
    config_path: Path = typer.Option(..., "--config"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
) -> None:
    try:
        result = _TRADING_LAUNCHER.run_paper_config(config_path)
    except TradingConfigurationError as error:
        raise typer.BadParameter(str(error)) from error
    _echo_run_result(result, output_format=output_format)


@run_app.command("live")
def live(
    config_path: Path = typer.Option(..., "--config"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
) -> None:
    try:
        result = _TRADING_LAUNCHER.run_live_config(config_path)
    except TradingConfigurationError as error:
        raise typer.BadParameter(str(error)) from error
    _echo_run_result(result, output_format=output_format)


@run_app.command("daemon")
def daemon(
    action: str = typer.Argument("status"),
    root: Path | None = typer.Option(None, "--root"),
    run_id: str | None = typer.Option(None, "--run-id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    config_path: Path | None = typer.Option(None, "--config"),
    foreground: bool = typer.Option(False, "--foreground/--background"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
) -> None:
    control = RunControl(_run_root(root))
    if action == "start":
        if mode is None or config_path is None:
            raise typer.BadParameter("daemon start requires --mode and --config")
        try:
            result = (
                control.run_foreground(mode=mode, config_path=config_path, run_id=run_id)
                if foreground
                else control.start_background(mode=mode, config_path=config_path, run_id=run_id)
            )
        except RunAlreadyActiveError as error:
            raise typer.BadParameter(str(error)) from error
        _echo({
            "run_id": result.run_id,
            "mode": result.mode,
            "run_instance_id": result.run_instance_id,
            "phase": result.phase,
            "directory": str(result.directory),
            "state_file": str(result.state_path),
            "summary_file": str(result.summary_path),
            "result": dict(result.result),
        })
        return
    if action == "stop":
        if mode is None or run_id is None:
            raise typer.BadParameter("daemon stop requires --mode and --run-id")
        path = control.request_stop(mode=mode, run_id=run_id, reason="requested by cli")
        _echo({"command_file": str(path), "mode": mode.value, "run_id": run_id, "desired_state": "stopped"})
        return
    if action != "status":
        raise typer.BadParameter(f"daemon action {action!r} is not supported by the rewritten runtime registry")
    records = control.list(mode=mode, run_id=run_id)
    _echo_registry(records, output_format=output_format)


@run_app.command("list")
def list_runs(
    root: Path | None = typer.Option(None, "--root"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    run_id: str | None = typer.Option(None, "--run-id"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
) -> None:
    records = RunControl(_run_root(root)).list(mode=mode, run_id=run_id)
    _echo_registry(records, output_format=output_format)


@run_app.command("shell")
def shell(
    command: list[str] | None = typer.Option(None, "--command"),
) -> None:
    session = RunShellSession()
    if command:
        for line in command:
            if session.handle(line):
                return
        return
    typer.echo(session.banner())
    typer.echo(session.menu())
    while True:
        try:
            line = input(session.prompt())
        except EOFError:
            typer.echo("")
            return
        except KeyboardInterrupt:
            typer.echo("\nUse `quit` to exit.")
            continue
        if session.handle(line):
            return


class RunShellSession:
    def __init__(self, *, stdout: TextIO | None = None) -> None:
        self.stdout = stdout or sys.stdout
        self.mode = RuntimeMode.BACKTEST
        self.run_id: str | None = None
        self.run_choices: list[dict[str, object]] = []

    def banner(self) -> str:
        return "Kairos run workspace."

    def prompt(self) -> str:
        return "kairospy/run> "

    def menu(self) -> str:
        return "\n".join([
            "Run Workspace",
            "",
            "Commands",
            "  config validate <path>",
            "  config explain <path>",
            "  register <name> <path>",
            "  unregister <name>",
            "  specs",
            "  validate <name-or-path>",
            "  explain <name-or-path>",
            "  start <name-or-path>",
            "  stop <name-or-run-id> --mode backtest",
            "  status <name-or-run-id>",
            "  logs <name-or-run-id>",
            "  artifacts <name-or-run-id>",
            "  daemon status",
            "  events --strategy module:callable --events events.jsonl",
            "  backtest --config run.toml  (low-level)",
            "  paper --config run.toml     (low-level)",
            "  live --config run.toml      (low-level)",
            "  list",
            "  quit",
        ])

    def handle(self, line: str) -> bool:
        parts = shlex.split(line.strip())
        if not parts:
            self._write(self.menu())
            return False
        command = parts[0]
        if command in {"quit", "exit", "q"}:
            return True
        if command in {"help", "?", "menu"}:
            self._write(self.menu())
            return False
        if command == "config" and len(parts) >= 3:
            return self._invoke([command, parts[1], parts[2]])
        if command in {
            "events",
            "backtest",
            "paper",
            "live",
            "list",
            "daemon",
            "register",
            "unregister",
            "specs",
            "validate",
            "explain",
            "start",
            "stop",
            "status",
            "logs",
            "artifacts",
        }:
            return self._invoke(parts)
        self._write(f"Unknown run shell command: {command}")
        return False

    def _invoke(self, argv: list[str]) -> bool:
        from typer.testing import CliRunner

        result = CliRunner().invoke(run_app, argv, catch_exceptions=False)
        if result.output:
            self._write(result.output.rstrip())
        return False

    def _write(self, text: str) -> None:
        self.stdout.write(text + "\n")
        self.stdout.flush()


def _echo_run_result(result: object, *, output_format: OutputFormat) -> None:
    runtime = getattr(result, "runtime")
    payload = {
        "run_id": getattr(result, "run_id"),
        "mode": getattr(result, "mode"),
        "runtime": runtime,
        "controls": getattr(result, "controls").list(),
    }
    if _use_json_output(output_format):
        _echo(payload)
        return
    typer.echo(
        "\n".join([
            f"Run {getattr(result, 'mode').value}:{getattr(result, 'run_id')}",
            f"  strategy  {runtime.strategy_id}",
            f"  events    {runtime.event_count}",
            f"  intents   {runtime.intent_count}",
            f"  controls  {len(getattr(result, 'controls').list())}",
        ])
    )


def _echo(payload: Mapping[str, object]) -> None:
    typer.echo(json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True))


def _echo_registry(records: tuple[object, ...], *, output_format: OutputFormat) -> None:
    items = [_record_payload(record) for record in records]
    payload = {"runs": items, "count": len(items)}
    if _use_json_output(output_format):
        _echo(payload)
        return
    if not items:
        typer.echo("Runs\n  none")
        return
    lines = ["Runs"]
    for item in items:
        detail = f"  log {item['log_file']}" if item.get("log_file") else ""
        lines.append(f"  {item['mode']}:{item['run_id']}  {item['directory']}{detail}")
    typer.echo("\n".join(lines))


def _echo_validation_text(payload: Mapping[str, object]) -> None:
    lines = [
        f"Run Config {payload['target']}",
        f"  path   {payload['path']}",
        f"  valid  {str(payload['valid']).lower()}",
    ]
    lines.extend(f"  issue  {issue}" for issue in payload["issues"] if isinstance(issue, str))
    typer.echo("\n".join(lines))


def _run_root(root: Path | None) -> Path:
    if root is not None:
        return root
    return KairosWorkspace.resolve().run_root


def _run_identity(
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
        raise typer.BadParameter("run command requires --mode when target is not a registered run/config")
    if require_mode and run_id is None:
        raise typer.BadParameter("run command requires a run id when target is not a registered run/config")
    return mode, run_id


def _single_run_record(
    target: str | None,
    *,
    mode: RuntimeMode | None,
    run_id: str | None,
    root: Path | None,
):
    resolved_mode, resolved_run_id = _run_identity(target, mode=mode, run_id=run_id, require_mode=False)
    records = RunControl(_run_root(root)).list(mode=resolved_mode, run_id=resolved_run_id)
    if not records:
        raise typer.BadParameter("run record was not found")
    if len(records) > 1:
        raise typer.BadParameter("multiple run records matched; pass a registered run, --mode, or --run-id")
    return records[0]


def _first_log_file(candidates: tuple[Path, ...]) -> Path | None:
    existing = [candidate for candidate in candidates if candidate.exists()]
    for candidate in existing:
        if candidate.stat().st_size > 0:
            return candidate
    return existing[0] if existing else None


def _record_payload(record: object) -> Mapping[str, object]:
    method = getattr(record, "to_dict", None)
    if callable(method):
        return method()
    return record if isinstance(record, Mapping) else {"record": record}


def _use_json_output(output_format: OutputFormat) -> bool:
    if output_format is OutputFormat.json:
        return True
    if output_format is OutputFormat.text:
        return False
    return not sys.stdout.isatty()


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
    if hasattr(value, "list"):
        return _jsonable(value.list())
    return value


__all__ = ["RunShellSession", "run_app"]
