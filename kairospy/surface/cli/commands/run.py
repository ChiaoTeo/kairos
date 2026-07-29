from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Mapping

import typer

from kairospy.application.system.facade.run import RunAlreadyActiveError, RunFacade, RuntimeMode, TradingConfigurationError, record_payload
from kairospy.surface.cli.options import OutputFormat, resolve_output
from kairospy.surface.cli.output import write_cli_result
from kairospy.surface.rendering.writer import write_result


run_app = typer.Typer(no_args_is_help=True, help="Run commands")
daemon_app = typer.Typer(no_args_is_help=False, help="Run daemon commands", invoke_without_command=True)
instance_app = typer.Typer(no_args_is_help=True, help="Run instance commands")
run_app.add_typer(daemon_app, name="daemon")
run_app.add_typer(instance_app, name="instance")
_RUNS = RunFacade()


@run_app.command("config")
def config(
    ctx: typer.Context,
    action: str = typer.Argument(...),
    path: Path = typer.Argument(...),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _RUNS.config(action=action, path=path)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_config_result)
    if action == "validate":
        if not payload["valid"]:
            raise typer.Exit(2)


@run_app.command("register")
def register(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Run name, or config path when CONFIG_PATH is omitted"),
    config_path: Path | None = typer.Argument(None, help="Run config path"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _RUNS.register_target(name_or_config_path=name, config_path=config_path)
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="CONFIG_PATH") from error
    write_cli_result(ctx, payload, output_format=output_format, text=_render_register)


@run_app.command("unregister")
def unregister(
    ctx: typer.Context,
    name: str = typer.Argument(...),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    write_cli_result(ctx, _RUNS.unregister(name), output_format=output_format, text=_render_unregister)


@run_app.command("specs")
def specs(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _RUNS.specs()
    output = resolve_output(ctx, output_format, default=OutputFormat.auto)
    if _use_json_output(output):
        _write_json(payload)
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
    _write_json(payload) if _use_json_output(output) else _echo_validation_text(payload)
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
        _write_json(payload)
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
    strategy_ref: str | None = typer.Option(None, "--strategy", help="Strategy import path override: module:callable"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _RUNS.start(target, strategy_ref=strategy_ref)
    except (RunAlreadyActiveError, TradingConfigurationError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    _echo_start_result(payload, output_format=resolve_output(ctx, output_format, default=OutputFormat.auto))


@run_app.command("stop")
def stop(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Registered run name or run id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    run_id: str | None = typer.Option(None, "--run-id"),
    root: Path | None = typer.Option(None, "--root"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _RUNS.stop(target=target, mode=mode, run_id=run_id, root=root)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_stop)


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
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow the selected run log."),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    if follow:
        if _use_json_output(resolve_output(ctx, output_format, default=OutputFormat.text)):
            raise typer.BadParameter("--follow requires text output")
        try:
            path = _RUNS.log_file(target=target, mode=mode, run_id=run_id, root=root)
        except ValueError as error:
            raise typer.BadParameter(str(error)) from error
        _tail_file(path)
        return
    try:
        payload = _RUNS.logs(target=target, mode=mode, run_id=run_id, root=root, limit=limit)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    if _use_json_output(resolve_output(ctx, output_format)):
        _write_json(payload)
        return
    if payload["log_file"] is None:
        typer.echo(f"Run Logs\n  none\n  directory {payload['run'].get('directory', '')}")
        return
    typer.echo("\n".join(str(line) for line in payload["lines"]))


@daemon_app.command("attach")
def daemon_attach(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Registered run name or run id"),
    root: Path | None = typer.Option(None, "--root"),
    run_id: str | None = typer.Option(None, "--run-id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
) -> None:
    _ = ctx
    try:
        path = _RUNS.log_file(target=target, mode=mode, run_id=run_id, root=root)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _tail_file(path)


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
        _write_json(payload)
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


@daemon_app.callback()
def daemon(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    result = _daemon_result(action="status", target=None, root=None, run_id=None, mode=None, config_path=None, foreground=False)
    if isinstance(result, Mapping):
        _write_json(result)
        return
    _echo_registry(result, output_format=resolve_output(ctx, output_format, default=OutputFormat.auto))


@daemon_app.command("start")
def daemon_start(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Registered run name or run config path"),
    root: Path | None = typer.Option(None, "--root"),
    run_id: str | None = typer.Option(None, "--run-id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    config_path: Path | None = typer.Option(None, "--config"),
    strategy_ref: str | None = typer.Option(None, "--strategy", help="Strategy import path override: module:callable"),
    foreground: bool = typer.Option(False, "--foreground/--background"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    result = _daemon_result(
        action="start",
        target=target,
        root=root,
        run_id=run_id,
        mode=mode,
        config_path=config_path,
        foreground=foreground,
        strategy_ref=strategy_ref,
    )
    output = resolve_output(ctx, output_format, default=OutputFormat.auto)
    if isinstance(result, Mapping):
        _echo_start_result(result, output_format=output)
        return
    _echo_registry(result, output_format=output)


@daemon_app.command("status")
def daemon_status(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Registered run name or run id"),
    root: Path | None = typer.Option(None, "--root"),
    run_id: str | None = typer.Option(None, "--run-id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    result = _daemon_result(action="status", target=target, root=root, run_id=run_id, mode=mode, config_path=None, foreground=False)
    if isinstance(result, Mapping):
        _write_json(result)
        return
    _echo_registry(result, output_format=resolve_output(ctx, output_format, default=OutputFormat.auto))


@daemon_app.command("stop")
def daemon_stop(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Registered run name or run id"),
    root: Path | None = typer.Option(None, "--root"),
    run_id: str | None = typer.Option(None, "--run-id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    result = _daemon_result(action="stop", target=target, root=root, run_id=run_id, mode=mode, config_path=None, foreground=False)
    output = resolve_output(ctx, output_format, default=OutputFormat.auto)
    if isinstance(result, Mapping):
        write_result(result, output=output)
        return
    _echo_registry(result, output_format=output)


def _daemon_result(
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
    try:
        return _RUNS.daemon(
            action=action,
            target=target,
            root=root,
            run_id=run_id,
            mode=mode,
            config_path=config_path,
            foreground=foreground,
            strategy_ref=strategy_ref,
        )
    except RunAlreadyActiveError as error:
        raise typer.BadParameter(str(error)) from error
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@run_app.command("list")
def list_runs(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _RUNS.list()
    output = resolve_output(ctx, output_format, default=OutputFormat.text)
    if _use_json_output(output):
        _write_json(payload)
        return
    typer.echo(_render_registered_run_table(payload))


@instance_app.command("list")
def list_instances(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Registered run name or run id"),
    root: Path | None = typer.Option(None, "--root"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    run_id: str | None = typer.Option(None, "--run-id"),
    details: bool = typer.Option(False, "--details", help="Include run directories and log paths in text output."),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    records = _RUNS.records(target=target, mode=mode, run_id=run_id, root=root)
    _echo_registry(records, output_format=resolve_output(ctx, output_format, default=OutputFormat.text), details=details)


def _echo_run_result(result: object, *, output_format: OutputFormat) -> None:
    runtime = getattr(result, "runtime")
    controls = getattr(result, "controls").list()
    payload = _run_result_payload(result, runtime=runtime, controls=controls)
    if _use_json_output(output_format):
        _write_json(payload)
        return
    typer.echo(
        "\n".join([
            f"Run {getattr(result, 'mode').value}:{getattr(result, 'run_id')}",
            f"  strategy  {runtime.strategy_id}",
            f"  events    {runtime.event_count}",
            f"  intents   {runtime.intent_count}",
            f"  controls  {len(controls)}",
            f"  equity    {payload.get('final_equity') or ''}",
            f"  pnl       {payload.get('net_profit') or ''}",
        ])
    )


def _echo_start_result(payload: Mapping[str, object], *, output_format: OutputFormat) -> None:
    if _use_json_output(output_format):
        _write_json(payload)
        return
    result = payload.get("result") if isinstance(payload.get("result"), Mapping) else {}
    typer.echo(
        "\n".join([
            f"Run {payload.get('mode')}:{payload.get('run_id')}",
            f"  instance  {payload.get('run_instance_id') or ''}",
            f"  phase     {payload.get('phase') or ''}",
            f"  strategy  {result.get('strategy_id') or ''}",
            f"  events    {result.get('event_count') or ''}",
            f"  intents   {result.get('intent_count') or ''}",
            f"  equity    {result.get('final_equity') or ''}",
            f"  pnl       {result.get('net_profit') or ''}",
            f"  directory {payload.get('directory') or ''}",
        ])
    )


def _run_result_payload(result: object, *, runtime: object, controls: object) -> dict[str, object]:
    return {
        "run_id": getattr(result, "run_id"),
        "mode": getattr(result, "mode"),
        "strategy_id": getattr(runtime, "strategy_id", None),
        "event_count": getattr(runtime, "event_count", None),
        "intent_count": getattr(runtime, "intent_count", None),
        "control_count": len(controls) if hasattr(controls, "__len__") else None,
        "fills": len(getattr(result, "fills", ())),
        "trades": len(getattr(result, "trades", ())),
        "decision_trace_count": len(getattr(result, "decision_trace", ())),
        "risk_snapshot_count": len(getattr(result, "risk_snapshots", ())),
        "initial_equity": getattr(result, "initial_equity", None),
        "final_equity": getattr(result, "final_equity", None),
        "net_profit": getattr(result, "net_profit", None),
        "total_return": getattr(result, "total_return", None),
        "metrics": getattr(result, "metrics", {}),
    }


def _write_json(payload: Mapping[str, object]) -> None:
    write_result(payload, output=OutputFormat.json)


def _echo_registry(records: tuple[object, ...], *, output_format: OutputFormat, details: bool = True) -> None:
    items = [record_payload(record) for record in records]
    payload = {"runs": items, "count": len(items)}
    if _use_json_output(output_format):
        _write_json(payload)
        return
    if not items:
        typer.echo("Runs\n  none")
        return
    typer.echo(_render_run_table(items, details=details))


def _echo_validation_text(payload: Mapping[str, object]) -> None:
    lines = [
        f"Run Config {payload['target']}",
        f"  path   {payload['path']}",
        f"  valid  {str(payload['valid']).lower()}",
    ]
    lines.extend(f"  issue  {issue}" for issue in payload["issues"] if isinstance(issue, str))
    typer.echo("\n".join(lines))


def _render_config_result(result: object) -> str:
    payload = _payload(result)
    if "valid" in payload:
        lines = [
            "Run Config",
            f"  path   {payload['path']}",
            f"  valid  {str(payload['valid']).lower()}",
        ]
        lines.extend(f"  issue  {issue}" for issue in payload.get("issues", ()) if isinstance(issue, str))
        return "\n".join(lines)
    return "\n".join([
        "Run Config",
        f"  path    {payload.get('path', '')}",
        f"  mode    {payload.get('mode', '')}",
        f"  run_id  {payload.get('run_id', '')}",
    ])


def _render_register(result: object) -> str:
    payload = _payload(result)
    return "\n".join([
        "Run Registered",
        f"  name    {payload['name']}",
        f"  config  {payload['config']}",
        f"  index   {payload['index']}",
    ])


def _render_unregister(result: object) -> str:
    payload = _payload(result)
    return "\n".join([
        "Run Unregistered",
        f"  name    {payload['name']}",
        f"  config  {payload['config']}",
        f"  index   {payload['index']}",
    ])


def _render_stop(result: object) -> str:
    payload = _payload(result)
    return "\n".join([
        f"Run Stop Requested {payload['mode']}:{payload['run_id']}",
        f"  desired_state  {payload['desired_state']}",
        f"  command_file   {payload['command_file']}",
    ])


def _render_registered_run_table(payload: Mapping[str, object]) -> str:
    items = payload.get("runs")
    if not isinstance(items, list) or not items:
        return f"Runs\n  none\n  index {payload.get('path', '')}"
    rows = tuple(_registered_run_table_row(item) for item in items if isinstance(item, Mapping))
    columns = ("name", "mode", "run_id", "strategy", "valid", "config", "registered")
    visible_columns = tuple(column for column in columns if any(row.get(column) not in {None, ""} for row in rows))
    widths = {
        column: max(len(column), *(len(_table_cell(row.get(column))) for row in rows))
        for column in visible_columns
    }
    lines = [
        "Runs",
        "  " + "  ".join(column.ljust(widths[column]) for column in visible_columns),
        "  " + "  ".join("-" * widths[column] for column in visible_columns),
    ]
    for row in rows:
        lines.append("  " + "  ".join(_table_cell(row.get(column)).ljust(widths[column]) for column in visible_columns))
    return "\n".join(lines)


def _registered_run_table_row(item: Mapping[str, object]) -> dict[str, object]:
    return {
        "name": item.get("name"),
        "mode": item.get("mode"),
        "run_id": item.get("run_id"),
        "strategy": item.get("strategy"),
        "valid": str(bool(item.get("valid"))).lower(),
        "config": _display_path(item.get("config")),
        "registered": _short_time(item.get("registered_at")),
    }


def _use_json_output(output_format: OutputFormat) -> bool:
    if output_format is OutputFormat.json:
        return True
    if output_format is OutputFormat.text:
        return False
    return not sys.stdout.isatty()


def _render_run_table(items: list[Mapping[str, object]], *, details: bool) -> str:
    rows = tuple(_run_table_row(item) for item in items)
    columns = [
        "mode",
        "run_id",
        "status",
        "strategy",
        "equity",
        "pnl",
        "events",
        "fills",
        "trades",
        "updated",
    ]
    if details:
        columns.extend(("phase", "return", "intents", "directory", "log"))
    columns = tuple(columns)
    visible_columns = tuple(column for column in columns if any(row.get(column) not in {None, ""} for row in rows))
    widths = {
        column: max(len(column), *(len(_table_cell(row.get(column))) for row in rows))
        for column in visible_columns
    }
    lines = [
        "Runs",
        "  " + "  ".join(column.ljust(widths[column]) for column in visible_columns),
        "  " + "  ".join("-" * widths[column] for column in visible_columns),
    ]
    for row in rows:
        lines.append("  " + "  ".join(_table_cell(row.get(column)).ljust(widths[column]) for column in visible_columns))
    return "\n".join(lines)


def _run_table_row(item: Mapping[str, object]) -> dict[str, object]:
    result = item.get("result") if isinstance(item.get("result"), Mapping) else {}
    context = item.get("context") if isinstance(item.get("context"), Mapping) else {}
    return {
        "mode": item.get("mode"),
        "run_id": item.get("run_id"),
        "status": item.get("status"),
        "phase": item.get("phase"),
        "strategy": context.get("strategy") or result.get("strategy_id"),
        "equity": result.get("final_equity"),
        "pnl": result.get("net_profit"),
        "return": result.get("total_return"),
        "events": result.get("event_count"),
        "intents": result.get("intent_count"),
        "fills": result.get("fills"),
        "trades": result.get("closed_trades"),
        "updated": _short_time(item.get("updated_at")),
        "directory": _display_path(item.get("directory")),
        "log": _display_path(item.get("log_file")),
    }


def _table_cell(value: object) -> str:
    if value is None:
        return "-"
    return str(value)


def _short_time(value: object) -> object:
    if not isinstance(value, str):
        return value
    return value.replace("T", " ")[:19]


def _display_path(value: object) -> object:
    if not isinstance(value, str) or not value:
        return value
    path = Path(value)
    if not path.is_absolute():
        return value
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return value


def _payload(result: object) -> Mapping[str, object]:
    if not isinstance(result, Mapping):
        raise TypeError("run renderer expected mapping payload")
    return result


def _tail_file(path: Path, *, interval_seconds: float = 0.5) -> None:
    path = Path(path)
    position = 0
    try:
        while True:
            if path.exists():
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(position)
                    chunk = handle.read()
                    position = handle.tell()
                if chunk:
                    typer.echo(chunk, nl=False)
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        return


__all__ = ["run_app"]
