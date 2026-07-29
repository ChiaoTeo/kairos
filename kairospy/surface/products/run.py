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
from kairospy.application.service.modes.backtest import BacktestConfigurationError
from kairospy.application.service.modes.live import LiveConfigurationError
from kairospy.application.service.modes.paper import PaperConfigurationError
from kairospy.application.system import TradingSystemLauncher
from kairospy.application.system.run import RunDaemonService, RunRegistry
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
    except BacktestConfigurationError as error:
        raise typer.BadParameter(str(error)) from error
    _echo_run_result(result, output_format=output_format)


@run_app.command("paper")
def paper(
    config_path: Path = typer.Option(..., "--config"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
) -> None:
    try:
        result = _TRADING_LAUNCHER.run_paper_config(config_path)
    except PaperConfigurationError as error:
        raise typer.BadParameter(str(error)) from error
    _echo_run_result(result, output_format=output_format)


@run_app.command("live")
def live(
    config_path: Path = typer.Option(..., "--config"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
) -> None:
    try:
        result = _TRADING_LAUNCHER.run_live_config(config_path)
    except LiveConfigurationError as error:
        raise typer.BadParameter(str(error)) from error
    _echo_run_result(result, output_format=output_format)


@run_app.command("daemon")
def daemon(
    action: str = typer.Argument("status"),
    root: Path = typer.Option(Path(".kairos/runs"), "--root"),
    run_id: str | None = typer.Option(None, "--run-id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    config_path: Path | None = typer.Option(None, "--config"),
    foreground: bool = typer.Option(False, "--foreground/--background"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
) -> None:
    registry = RunRegistry(root)
    if action == "start":
        if mode is None or config_path is None:
            raise typer.BadParameter("daemon start requires --mode and --config")
        service = RunDaemonService(root)
        result = (
            service.run_foreground(mode=mode, config_path=config_path, run_id=run_id)
            if foreground
            else service.start_background(mode=mode, config_path=config_path, run_id=run_id)
        )
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
        path = registry.request_stop(mode=mode.value, run_id=run_id, reason="requested by cli")
        _echo({"command_file": str(path), "mode": mode.value, "run_id": run_id, "desired_state": "stopped"})
        return
    if action != "status":
        raise typer.BadParameter(f"daemon action {action!r} is not supported by the rewritten runtime registry")
    records = registry.list(mode=None if mode is None else mode.value, run_id=run_id)
    _echo_registry(records, output_format=output_format)


@run_app.command("list")
def list_runs(
    root: Path = typer.Option(Path(".kairos/runs"), "--root"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    run_id: str | None = typer.Option(None, "--run-id"),
    output_format: OutputFormat = typer.Option(OutputFormat.auto, "--format"),
) -> None:
    records = RunRegistry(root).list(mode=None if mode is None else mode.value, run_id=run_id)
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
            "  events --strategy module:callable --events events.jsonl",
            "  backtest --config run.toml",
            "  paper --config run.toml",
            "  live --config run.toml",
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
        if command in {"events", "backtest", "paper", "live", "list", "daemon"}:
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
    payload = {"runs": records, "count": len(records)}
    if _use_json_output(output_format):
        _echo(payload)
        return
    if not records:
        typer.echo("Runs\n  none")
        return
    lines = ["Runs"]
    for record in records:
        lines.append(f"  {record.mode}:{record.run_id}  {record.directory}")
    typer.echo("\n".join(lines))


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
