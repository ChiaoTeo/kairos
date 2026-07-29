from __future__ import annotations

import sys
from pathlib import Path
from typing import Mapping

import typer

from kairospy.application.system.facade.run import RunAlreadyActiveError, RunFacade, RuntimeMode, TradingConfigurationError, record_payload
from kairospy.surface.cli.options import OutputFormat, resolve_output
from kairospy.surface.rendering.writer import write_result


run_app = typer.Typer(no_args_is_help=True, help="Run commands")
_RUNS = RunFacade()


@run_app.command("config")
def config(
    action: str = typer.Argument(...),
    path: Path = typer.Argument(...),
) -> None:
    try:
        payload = _RUNS.config(action=action, path=path)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _echo(payload)
    if action == "validate":
        if not payload["valid"]:
            raise typer.Exit(2)


@run_app.command("register")
def register(
    name: str = typer.Argument(...),
    config_path: Path = typer.Argument(...),
) -> None:
    _echo(_RUNS.register(name=name, config_path=config_path))


@run_app.command("unregister")
def unregister(name: str = typer.Argument(...)) -> None:
    _echo(_RUNS.unregister(name))


@run_app.command("specs")
def specs(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _RUNS.specs()
    output = resolve_output(ctx, output_format, default=OutputFormat.auto)
    if _use_json_output(output):
        _echo(payload)
        return
    entries = payload.get("runs", {})
    if not entries:
        typer.echo(f"Run Specs\n  none\n  index {payload.get('path', '')}")
        return
    lines = ["Run Specs"]
    for name, entry in entries.items():
        config = entry.get("config") if isinstance(entry, Mapping) else entry
        lines.append(f"  {name}  {config}")
    typer.echo("\n".join(lines))


@run_app.command("validate")
def validate(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Registered run name or run config path"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _RUNS.validate(target)
    output = resolve_output(ctx, output_format, default=OutputFormat.auto)
    _echo(payload) if _use_json_output(output) else _echo_validation_text(payload)
    if not payload["valid"]:
        raise typer.Exit(2)


@run_app.command("explain")
def explain(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Registered run name or run config path"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _RUNS.explain(target)
    if _use_json_output(resolve_output(ctx, output_format, default=OutputFormat.auto)):
        _echo(payload)
        return
    lines = [
        f"Run Config {target}",
        f"  path       {payload['path']}",
        f"  mode       {payload['mode']}",
        f"  run_id     {payload['run_id']}",
        f"  strategy   {payload['strategy'] or ''}",
        f"  account    {payload['account_ref'] or ''}",
        f"  source     {payload['sources']['account'] or ''}",
    ]
    typer.echo("\n".join(lines))


@run_app.command("start")
def start(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Registered run name or run config path"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        result = _RUNS.start(target)
    except (TradingConfigurationError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    _echo_run_result(result, output_format=resolve_output(ctx, output_format, default=OutputFormat.auto))


@run_app.command("stop")
def stop(
    target: str | None = typer.Argument(None, help="Registered run name or run id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    run_id: str | None = typer.Option(None, "--run-id"),
    root: Path | None = typer.Option(None, "--root"),
) -> None:
    try:
        _echo(_RUNS.stop(target=target, mode=mode, run_id=run_id, root=root))
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@run_app.command("status")
def status(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Registered run name or run id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    run_id: str | None = typer.Option(None, "--run-id"),
    root: Path | None = typer.Option(None, "--root"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    records = _RUNS.records(target=target, mode=mode, run_id=run_id, root=root)
    _echo_registry(records, output_format=resolve_output(ctx, output_format, default=OutputFormat.auto))


@run_app.command("logs")
def logs(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Registered run name or run id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    run_id: str | None = typer.Option(None, "--run-id"),
    root: Path | None = typer.Option(None, "--root"),
    limit: int = typer.Option(100, "--limit"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _RUNS.logs(target=target, mode=mode, run_id=run_id, root=root, limit=limit)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    if _use_json_output(resolve_output(ctx, output_format)):
        _echo(payload)
        return
    if payload["log_file"] is None:
        typer.echo(f"Run Logs\n  none\n  directory {payload['run'].get('directory', '')}")
        return
    typer.echo("\n".join(str(line) for line in payload["lines"]))


@run_app.command("artifacts")
def artifacts(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Registered run name or run id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    run_id: str | None = typer.Option(None, "--run-id"),
    root: Path | None = typer.Option(None, "--root"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _RUNS.artifacts(target=target, mode=mode, run_id=run_id, root=root)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    if _use_json_output(resolve_output(ctx, output_format, default=OutputFormat.auto)):
        _echo(payload)
        return
    files = payload.get("files")
    files = files if isinstance(files, list) else []
    directory = payload.get("directory") or payload.get("run", {}).get("directory", "")
    if not files:
        typer.echo(f"Run Artifacts\n  none\n  directory {directory}")
        return
    lines = ["Run Artifacts", f"  directory {directory}"]
    lines.extend(f"  {item['size']:>8}  {item['path']}" for item in files)
    typer.echo("\n".join(lines))


@run_app.command("events")
def events(
    ctx: typer.Context,
    strategy_path: str = typer.Option(..., "--strategy", help="Strategy import path: module:callable"),
    events_path: Path = typer.Option(..., "--events", help="JSONL RuntimeEnvelope file"),
    run_id: str = typer.Option("kairos-run", "--run-id"),
    mode: RuntimeMode = typer.Option(RuntimeMode.BACKTEST, "--mode"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        result = _RUNS.run_events(strategy_path=strategy_path, events_path=events_path, run_id=run_id, mode=mode)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _echo_run_result(result, output_format=resolve_output(ctx, output_format, default=OutputFormat.auto))


@run_app.command("daemon")
def daemon(
    ctx: typer.Context,
    action: str = typer.Argument("status"),
    root: Path | None = typer.Option(None, "--root"),
    run_id: str | None = typer.Option(None, "--run-id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    config_path: Path | None = typer.Option(None, "--config"),
    foreground: bool = typer.Option(False, "--foreground/--background"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        result = _RUNS.daemon(action=action, root=root, run_id=run_id, mode=mode, config_path=config_path, foreground=foreground)
    except RunAlreadyActiveError as error:
        raise typer.BadParameter(str(error)) from error
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    if isinstance(result, Mapping):
        _echo(result)
        return
    _echo_registry(result, output_format=resolve_output(ctx, output_format, default=OutputFormat.auto))


@run_app.command("list")
def list_runs(
    ctx: typer.Context,
    root: Path | None = typer.Option(None, "--root"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    run_id: str | None = typer.Option(None, "--run-id"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    records = _RUNS.records(mode=mode, run_id=run_id, root=root)
    _echo_registry(records, output_format=resolve_output(ctx, output_format, default=OutputFormat.auto))


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
    write_result(payload, output=OutputFormat.json)


def _echo_registry(records: tuple[object, ...], *, output_format: OutputFormat) -> None:
    items = [record_payload(record) for record in records]
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


def _use_json_output(output_format: OutputFormat) -> bool:
    if output_format is OutputFormat.json:
        return True
    if output_format is OutputFormat.text:
        return False
    return not sys.stdout.isatty()


__all__ = ["run_app"]
