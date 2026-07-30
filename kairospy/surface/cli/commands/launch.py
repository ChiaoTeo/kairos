from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Mapping

import typer

from kairospy.application.launch.facade import DEFAULT_SYSTEM_LAUNCH_ID, LaunchAlreadyActiveError, LaunchFacade, RuntimeMode, TradingConfigurationError, record_payload
from kairospy.surface.cli.options import OutputFormat, resolve_output
from kairospy.surface.cli.output import write_cli_result
from kairospy.surface.rendering.writer import write_result


launch_app = typer.Typer(no_args_is_help=True, help="Launch commands")
targets_app = typer.Typer(no_args_is_help=True, help="Launch target commands")
run_app = typer.Typer(no_args_is_help=True, help="Launch run commands")
observe_app = typer.Typer(no_args_is_help=True, help="Launch observability commands")
diagnose_app = typer.Typer(no_args_is_help=True, help="Launch diagnostics commands")
daemon_app = typer.Typer(no_args_is_help=False, help="Launch daemon commands", invoke_without_command=True)
replay_app = typer.Typer(no_args_is_help=True, help="Launch replay commands")
system_app = typer.Typer(no_args_is_help=True, help="Built-in system runtime commands")
launch_app.add_typer(targets_app, name="targets")
launch_app.add_typer(run_app, name="run")
launch_app.add_typer(observe_app, name="observe")
launch_app.add_typer(diagnose_app, name="diagnose")
launch_app.add_typer(daemon_app, name="daemon")
launch_app.add_typer(replay_app, name="replay")
launch_app.add_typer(system_app, name="system")
_RUNS = LaunchFacade()


@targets_app.command("add")
def add_target(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Launch name, or config path when CONFIG_PATH is omitted"),
    config_path: Path | None = typer.Argument(None, help="Launch config path"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _RUNS.register_target(name_or_config_path=name, config_path=config_path)
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="CONFIG_PATH") from error
    write_cli_result(ctx, payload, output_format=output_format, text=_render_register)


@targets_app.command("remove")
def remove_target(
    ctx: typer.Context,
    name: str = typer.Argument(...),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    write_cli_result(ctx, _RUNS.unregister(name), output_format=output_format, text=_render_unregister)


@targets_app.command("index")
def target_index(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _RUNS.specs()
    output = resolve_output(ctx, output_format, default=OutputFormat.auto)
    if _use_json_output(output):
        _write_json(payload)
        return
    entries = payload.get("launches", {})
    if not entries:
        typer.echo(f"Launch Specs\n  none\n  index {payload.get('path', '')}")
        return
    lines = ["Launch Specs"]
    for name, entry in entries.items():
        config = entry.get("config") if isinstance(entry, Mapping) else entry
        lines.append(f"  {name}  {config}")
    typer.echo("\n".join(lines))


@diagnose_app.command("validate")
def validate(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Registered launch name or launch config path"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _RUNS.validate(target)
    output = resolve_output(ctx, output_format, default=OutputFormat.auto)
    _write_json(payload) if _use_json_output(output) else _echo_validation_text(payload)
    if not payload["valid"]:
        raise typer.Exit(2)


@diagnose_app.command("explain")
def explain(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Registered launch name or launch config path"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _RUNS.explain(target)
    if _use_json_output(resolve_output(ctx, output_format, default=OutputFormat.auto)):
        _write_json(payload)
        return
    lines = [
        f"Launch Config {target}",
        f"  path       {payload['path']}",
        f"  mode       {payload['mode']}",
        f"  launch_id     {payload['launch_id']}",
        f"  strategy   {payload['strategy'] or ''}",
        f"  account    {payload['account_ref'] or ''}",
        f"  source     {payload['sources']['account'] or ''}",
    ]
    typer.echo("\n".join(lines))


@run_app.command("start")
def start(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Registered launch name or launch config path"),
    strategy_ref: str | None = typer.Option(None, "--strategy", help="Strategy import path override: module:callable"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _RUNS.start(target, strategy_ref=strategy_ref)
    except (LaunchAlreadyActiveError, TradingConfigurationError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    _echo_start_result(payload, output_format=resolve_output(ctx, output_format, default=OutputFormat.auto))


@run_app.command("stop")
def stop(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Registered launch name or launch id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    launch_id: str | None = typer.Option(None, "--launch-id"),
    root: Path | None = typer.Option(None, "--root"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _RUNS.stop(target=target, mode=mode, launch_id=launch_id, root=root)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_stop)


@run_app.command("status")
def status(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Registered launch name or launch id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    launch_id: str | None = typer.Option(None, "--launch-id"),
    root: Path | None = typer.Option(None, "--root"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    records = _RUNS.records(target=target, mode=mode, launch_id=launch_id, root=root)
    _echo_registry(records, output_format=resolve_output(ctx, output_format, default=OutputFormat.auto))


@observe_app.command("logs")
def logs(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Registered launch name or launch id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    launch_id: str | None = typer.Option(None, "--launch-id"),
    root: Path | None = typer.Option(None, "--root"),
    limit: int = typer.Option(100, "--limit"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow the selected launch log."),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    if follow:
        if _use_json_output(resolve_output(ctx, output_format, default=OutputFormat.text)):
            raise typer.BadParameter("--follow requires text output")
        try:
            path = _RUNS.log_file(target=target, mode=mode, launch_id=launch_id, root=root)
        except ValueError as error:
            raise typer.BadParameter(str(error)) from error
        _tail_file(path)
        return
    try:
        payload = _RUNS.logs(target=target, mode=mode, launch_id=launch_id, root=root, limit=limit)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    if _use_json_output(resolve_output(ctx, output_format)):
        _write_json(payload)
        return
    if payload["log_file"] is None:
        typer.echo(f"Launch Logs\n  none\n  directory {payload['launch'].get('directory', '')}")
        return
    typer.echo("\n".join(str(line) for line in payload["lines"]))


@daemon_app.command("attach")
def daemon_attach(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Registered launch name or launch id"),
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str | None = typer.Option(None, "--launch-id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
) -> None:
    _ = ctx
    try:
        path = _RUNS.log_file(target=target, mode=mode, launch_id=launch_id, root=root)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _tail_file(path)


@observe_app.command("artifacts")
def artifacts(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Registered launch name or launch id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    launch_id: str | None = typer.Option(None, "--launch-id"),
    root: Path | None = typer.Option(None, "--root"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _RUNS.artifacts(target=target, mode=mode, launch_id=launch_id, root=root)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    if _use_json_output(resolve_output(ctx, output_format, default=OutputFormat.auto)):
        _write_json(payload)
        return
    files = payload.get("artifacts")
    files = files if isinstance(files, list) else []
    directory = payload.get("directory") or payload.get("launch", {}).get("directory", "")
    if not files:
        typer.echo(f"Launch Artifacts\n  none\n  directory {directory}")
        return
    lines = ["Launch Artifacts", f"  directory {directory}"]
    lines.extend(f"  {item['size']:>8}  {item['path']}" for item in files)
    typer.echo("\n".join(lines))


@replay_app.command("events")
def events(
    ctx: typer.Context,
    strategy_path: str = typer.Option(..., "--strategy", help="Strategy import path: module:callable"),
    events_path: Path = typer.Option(..., "--events", help="JSONL RuntimeEnvelope file"),
    launch_id: str = typer.Option("kairos-launch", "--launch-id"),
    mode: RuntimeMode = typer.Option(RuntimeMode.BACKTEST, "--mode"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        result = _RUNS.launch_events(strategy_path=strategy_path, events_path=events_path, launch_id=launch_id, mode=mode)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _echo_launch_result(result, output_format=resolve_output(ctx, output_format, default=OutputFormat.auto))


@system_app.command("up")
def system_up(
    ctx: typer.Context,
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str = typer.Option(DEFAULT_SYSTEM_LAUNCH_ID, "--launch-id"),
    foreground: bool = typer.Option(False, "--foreground/--background"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _RUNS.system_up(root=root, launch_id=launch_id, foreground=foreground)
    except LaunchAlreadyActiveError as error:
        raise typer.BadParameter(str(error)) from error
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _echo_start_result(payload, output_format=resolve_output(ctx, output_format, default=OutputFormat.auto))


@system_app.command("down")
def system_down(
    ctx: typer.Context,
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str = typer.Option(DEFAULT_SYSTEM_LAUNCH_ID, "--launch-id"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _RUNS.system_down(root=root, launch_id=launch_id)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_stop)


@system_app.command("restart")
def system_restart(
    ctx: typer.Context,
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str = typer.Option(DEFAULT_SYSTEM_LAUNCH_ID, "--launch-id"),
    foreground: bool = typer.Option(False, "--foreground/--background"),
    timeout_seconds: float = typer.Option(10.0, "--timeout", help="Seconds to wait for the current system runtime to stop."),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _RUNS.system_restart(root=root, launch_id=launch_id, foreground=foreground, timeout_seconds=timeout_seconds)
    except (LaunchAlreadyActiveError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_restart)


@system_app.command("command")
def system_command(
    ctx: typer.Context,
    kind: str = typer.Argument(..., help="System command kind, for example account.current or runtime.stop"),
    payload_json: str | None = typer.Option(None, "--payload-json", help="Command payload JSON object"),
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str = typer.Option(DEFAULT_SYSTEM_LAUNCH_ID, "--launch-id"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for the system runtime response."),
    timeout_seconds: float = typer.Option(5.0, "--timeout"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _RUNS.system_command(
            kind=kind,
            payload=_json_object(payload_json),
            root=root,
            launch_id=launch_id,
            wait=wait,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@system_app.command("trade-status")
def system_trade_status(
    ctx: typer.Context,
    account: str | None = typer.Argument(None, help="Optional account id or broker.account key"),
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str = typer.Option(DEFAULT_SYSTEM_LAUNCH_ID, "--launch-id"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for the system runtime response."),
    timeout_seconds: float = typer.Option(5.0, "--timeout"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _system_trade_command(
        "account.trade-status",
        account=account,
        root=root,
        launch_id=launch_id,
        wait=wait,
        timeout_seconds=timeout_seconds,
    )
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@system_app.command("trade-acquire")
def system_trade_acquire(
    ctx: typer.Context,
    account: str = typer.Argument(..., help="Account id or broker.account key"),
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str = typer.Option(DEFAULT_SYSTEM_LAUNCH_ID, "--launch-id"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for the system runtime response."),
    timeout_seconds: float = typer.Option(5.0, "--timeout"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _system_trade_command(
        "account.trade-acquire",
        account=account,
        root=root,
        launch_id=launch_id,
        wait=wait,
        timeout_seconds=timeout_seconds,
    )
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@system_app.command("trade-release")
def system_trade_release(
    ctx: typer.Context,
    account: str = typer.Argument(..., help="Account id or broker.account key"),
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str = typer.Option(DEFAULT_SYSTEM_LAUNCH_ID, "--launch-id"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for the system runtime response."),
    timeout_seconds: float = typer.Option(5.0, "--timeout"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _system_trade_command(
        "account.trade-release",
        account=account,
        root=root,
        launch_id=launch_id,
        wait=wait,
        timeout_seconds=timeout_seconds,
    )
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@system_app.command("attach")
def system_attach(
    ctx: typer.Context,
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str = typer.Option(DEFAULT_SYSTEM_LAUNCH_ID, "--launch-id"),
) -> None:
    _ = ctx
    try:
        path = _RUNS.system_log_file(root=root, launch_id=launch_id)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _tail_file(path)


def _system_trade_command(
    kind: str,
    *,
    account: str | None,
    root: Path | None,
    launch_id: str,
    wait: bool,
    timeout_seconds: float,
) -> dict[str, object]:
    payload = {"account": account} if account is not None else {}
    try:
        return _RUNS.system_command(
            kind=kind,
            payload=payload,
            root=root,
            launch_id=launch_id,
            wait=wait,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@daemon_app.callback()
def daemon(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    result = _daemon_result(action="status", target=None, root=None, launch_id=None, mode=None, config_path=None, foreground=False)
    if isinstance(result, Mapping):
        _write_json(result)
        return
    _echo_registry(result, output_format=resolve_output(ctx, output_format, default=OutputFormat.auto))


@daemon_app.command("start")
def daemon_start(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Registered launch name or launch config path"),
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str | None = typer.Option(None, "--launch-id"),
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
        launch_id=launch_id,
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
    target: str | None = typer.Argument(None, help="Registered launch name or launch id"),
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str | None = typer.Option(None, "--launch-id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    result = _daemon_result(action="status", target=target, root=root, launch_id=launch_id, mode=mode, config_path=None, foreground=False)
    if isinstance(result, Mapping):
        _write_json(result)
        return
    _echo_registry(result, output_format=resolve_output(ctx, output_format, default=OutputFormat.auto))


@daemon_app.command("stop")
def daemon_stop(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Registered launch name or launch id"),
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str | None = typer.Option(None, "--launch-id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    result = _daemon_result(action="stop", target=target, root=root, launch_id=launch_id, mode=mode, config_path=None, foreground=False)
    output = resolve_output(ctx, output_format, default=OutputFormat.auto)
    if isinstance(result, Mapping):
        write_result(result, output=output)
        return
    _echo_registry(result, output_format=output)


@daemon_app.command("command")
def daemon_command(
    ctx: typer.Context,
    kind: str = typer.Argument(..., help="System command kind, for example runtime.stop or account.current"),
    target: str | None = typer.Argument(None, help="Registered launch name or launch id"),
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str | None = typer.Option(None, "--launch-id"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    payload_json: str | None = typer.Option(None, "--payload-json", help="Command payload JSON object"),
    wait: bool = typer.Option(False, "--wait", help="Wait for the running daemon to write a command response."),
    timeout_seconds: float = typer.Option(5.0, "--timeout", help="Seconds to wait for --wait command responses."),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _RUNS.submit_command(
            target=target,
            root=root,
            launch_id=launch_id,
            mode=mode,
            kind=kind,
            payload=_json_object(payload_json),
            wait=wait,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


def _daemon_result(
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
    try:
        return _RUNS.daemon(
            action=action,
            target=target,
            root=root,
            launch_id=launch_id,
            mode=mode,
            config_path=config_path,
            foreground=foreground,
            strategy_ref=strategy_ref,
        )
    except LaunchAlreadyActiveError as error:
        raise typer.BadParameter(str(error)) from error
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


def _json_object(value: str | None) -> Mapping[str, object] | None:
    if value is None:
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise typer.BadParameter(f"--payload-json must be a JSON object: {error}") from error
    if not isinstance(payload, Mapping):
        raise typer.BadParameter("--payload-json must be a JSON object")
    return payload


@targets_app.command("list")
def list_launches(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _RUNS.list()
    output = resolve_output(ctx, output_format, default=OutputFormat.text)
    if _use_json_output(output):
        _write_json(payload)
        return
    typer.echo(_render_registered_launch_table(payload))


@observe_app.command("instances")
def list_instances(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Registered launch name or launch id"),
    root: Path | None = typer.Option(None, "--root"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    launch_id: str | None = typer.Option(None, "--launch-id"),
    details: bool = typer.Option(False, "--details", help="Include launch directories and log paths in text output."),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    records = _RUNS.records(target=target, mode=mode, launch_id=launch_id, root=root)
    _echo_registry(records, output_format=resolve_output(ctx, output_format, default=OutputFormat.text), details=details)


def _echo_launch_result(result: object, *, output_format: OutputFormat) -> None:
    runtime = getattr(result, "runtime")
    controls = getattr(result, "controls").list()
    payload = _launch_result_payload(result, runtime=runtime, controls=controls)
    if _use_json_output(output_format):
        _write_json(payload)
        return
    typer.echo(
        "\n".join([
            f"Launch {getattr(result, 'mode').value}:{getattr(result, 'launch_id')}",
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
            f"Launch {payload.get('mode')}:{payload.get('launch_id')}",
            f"  instance  {payload.get('launch_instance_id') or ''}",
            f"  phase     {payload.get('phase') or ''}",
            f"  strategy  {result.get('strategy_id') or ''}",
            f"  events    {result.get('event_count') or ''}",
            f"  intents   {result.get('intent_count') or ''}",
            f"  equity    {result.get('final_equity') or ''}",
            f"  pnl       {result.get('net_profit') or ''}",
            f"  directory {payload.get('directory') or ''}",
        ])
    )


def _launch_result_payload(result: object, *, runtime: object, controls: object) -> dict[str, object]:
    return {
        "launch_id": getattr(result, "launch_id"),
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
    payload = {"launches": items, "count": len(items)}
    if _use_json_output(output_format):
        _write_json(payload)
        return
    if not items:
        typer.echo("Launches\n  none")
        return
    typer.echo(_render_launch_table(items, details=details))


def _echo_validation_text(payload: Mapping[str, object]) -> None:
    lines = [
        f"Launch Config {payload['target']}",
        f"  path   {payload['path']}",
        f"  valid  {str(payload['valid']).lower()}",
    ]
    lines.extend(f"  issue  {issue}" for issue in payload["issues"] if isinstance(issue, str))
    typer.echo("\n".join(lines))


def _render_register(result: object) -> str:
    payload = _payload(result)
    return "\n".join([
        "Launch Registered",
        f"  name    {payload['name']}",
        f"  config  {payload['config']}",
        f"  index   {payload['index']}",
    ])


def _render_unregister(result: object) -> str:
    payload = _payload(result)
    return "\n".join([
        "Launch Unregistered",
        f"  name    {payload['name']}",
        f"  config  {payload['config']}",
        f"  index   {payload['index']}",
    ])


def _render_stop(result: object) -> str:
    payload = _payload(result)
    return "\n".join([
        f"Launch Stop Requested {payload['mode']}:{payload['launch_id']}",
        f"  desired_state  {payload['desired_state']}",
        f"  command_file   {payload['command_file']}",
    ])


def _render_restart(result: object) -> str:
    payload = _payload(result)
    started = payload.get("started") if isinstance(payload.get("started"), Mapping) else {}
    stopped = payload.get("stopped") if isinstance(payload.get("stopped"), Mapping) else None
    lines = [
        f"System Restarted {started.get('mode')}:{started.get('launch_id')}",
        f"  instance  {started.get('launch_instance_id') or ''}",
        f"  phase     {started.get('phase') or ''}",
        f"  directory {started.get('directory') or ''}",
    ]
    if stopped is not None:
        lines.insert(1, f"  stopped   {stopped.get('command_file') or ''}")
    return "\n".join(lines)


def _render_registered_launch_table(payload: Mapping[str, object]) -> str:
    items = payload.get("launches")
    if not isinstance(items, list) or not items:
        return f"Launches\n  none\n  index {payload.get('path', '')}"
    rows = tuple(_registered_launch_table_row(item) for item in items if isinstance(item, Mapping))
    columns = ("name", "mode", "launch_id", "strategy", "valid", "config", "registered")
    visible_columns = tuple(column for column in columns if any(row.get(column) not in {None, ""} for row in rows))
    widths = {
        column: max(len(column), *(len(_table_cell(row.get(column))) for row in rows))
        for column in visible_columns
    }
    lines = [
        "Launches",
        "  " + "  ".join(column.ljust(widths[column]) for column in visible_columns),
        "  " + "  ".join("-" * widths[column] for column in visible_columns),
    ]
    for row in rows:
        lines.append("  " + "  ".join(_table_cell(row.get(column)).ljust(widths[column]) for column in visible_columns))
    return "\n".join(lines)


def _registered_launch_table_row(item: Mapping[str, object]) -> dict[str, object]:
    return {
        "name": item.get("name"),
        "mode": item.get("mode"),
        "launch_id": item.get("launch_id"),
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


def _render_launch_table(items: list[Mapping[str, object]], *, details: bool) -> str:
    rows = tuple(_launch_table_row(item) for item in items)
    columns = [
        "mode",
        "launch_id",
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
        "Launches",
        "  " + "  ".join(column.ljust(widths[column]) for column in visible_columns),
        "  " + "  ".join("-" * widths[column] for column in visible_columns),
    ]
    for row in rows:
        lines.append("  " + "  ".join(_table_cell(row.get(column)).ljust(widths[column]) for column in visible_columns))
    return "\n".join(lines)


def _launch_table_row(item: Mapping[str, object]) -> dict[str, object]:
    result = item.get("result") if isinstance(item.get("result"), Mapping) else {}
    context = item.get("context") if isinstance(item.get("context"), Mapping) else {}
    return {
        "mode": item.get("mode"),
        "launch_id": item.get("launch_id"),
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
        raise TypeError("launch renderer expected mapping payload")
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


__all__ = ["launch_app"]
