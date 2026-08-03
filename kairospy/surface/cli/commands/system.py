from __future__ import annotations

from pathlib import Path

import typer

from kairospy.application.support.system.application.control.facade import DEFAULT_SYSTEM_LAUNCH_ID, LaunchAlreadyActiveError, LaunchFacade
from kairospy.application.support.system.application.control.attach import LaunchAttachSession
from kairospy.surface.cli.options import OutputFormat, resolve_output
from kairospy.surface.cli.output import write_cli_result
from kairospy.surface.interactive.attach import RuntimeAttachShell
from kairospy.surface.tui.attach import RuntimeAttachApp


system_app = typer.Typer(no_args_is_help=True, help="Built-in system runtime commands")
system_account_app = typer.Typer(no_args_is_help=True, help="System account commands")
system_app.add_typer(system_account_app, name="account")
_RUNS = LaunchFacade()


@system_app.command("up")
def up(
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
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@system_app.command("down")
def down(
    ctx: typer.Context,
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str = typer.Option(DEFAULT_SYSTEM_LAUNCH_ID, "--launch-id"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _RUNS.system_down(root=root, launch_id=launch_id)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@system_app.command("restart")
def restart(
    ctx: typer.Context,
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str = typer.Option(DEFAULT_SYSTEM_LAUNCH_ID, "--launch-id"),
    foreground: bool = typer.Option(False, "--foreground/--background"),
    timeout_seconds: float = typer.Option(10.0, "--timeout", help="Seconds to wait for the current system runtime to stop."),
    clean_stale: bool = typer.Option(False, "--clean-stale", help="Terminate the registry current stale system PID after verifying it belongs to this launch."),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    from kairospy.surface.cli.commands.launch import _render_restart

    try:
        payload = _RUNS.system_restart(root=root, launch_id=launch_id, foreground=foreground, timeout_seconds=timeout_seconds, clean_stale=clean_stale)
    except (LaunchAlreadyActiveError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_restart)


@system_app.command("status")
def status(
    ctx: typer.Context,
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str = typer.Option(DEFAULT_SYSTEM_LAUNCH_ID, "--launch-id"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    from kairospy.surface.cli.commands.launch import _echo_registry

    records = _RUNS.system_records(root=root, launch_id=launch_id)
    _echo_registry(records, output_format=resolve_output(ctx, output_format, default=OutputFormat.auto))


@system_app.command("inspect")
def inspect(
    ctx: typer.Context,
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str = typer.Option(DEFAULT_SYSTEM_LAUNCH_ID, "--launch-id"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    from kairospy.surface.cli.commands.launch import _render_system_inspect

    payload = _RUNS.system_inspect(root=root, launch_id=launch_id)
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_system_inspect)


@system_app.command("command")
def command(
    ctx: typer.Context,
    kind: str = typer.Argument(..., help="System command kind, for example account.current or runtime.stop"),
    payload_json: str | None = typer.Option(None, "--payload-json", help="Command payload JSON object"),
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str = typer.Option(DEFAULT_SYSTEM_LAUNCH_ID, "--launch-id"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for the system runtime response."),
    timeout_seconds: float = typer.Option(5.0, "--timeout"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    from kairospy.surface.cli.commands.launch import _json_object

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


@system_app.command("attach")
def attach(
    ctx: typer.Context,
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str = typer.Option(DEFAULT_SYSTEM_LAUNCH_ID, "--launch-id"),
    shell: bool = typer.Option(False, "--shell", help="Use the line-oriented attach shell instead of the Textual app."),
) -> None:
    _ = ctx
    try:
        session = LaunchAttachSession.system(root=root, launch_id=launch_id, launches=_RUNS)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    if shell:
        RuntimeAttachShell(session).run()
        return
    RuntimeAttachApp(session).run()


@system_account_app.command("trade-status")
def account_trade_status(
    ctx: typer.Context,
    account: str | None = typer.Argument(None, help="Optional account id or broker.account key"),
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str = typer.Option(DEFAULT_SYSTEM_LAUNCH_ID, "--launch-id"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for the system runtime response."),
    timeout_seconds: float = typer.Option(5.0, "--timeout"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _system_account_command(
        "account.trade-status",
        account=account,
        root=root,
        launch_id=launch_id,
        wait=wait,
        timeout_seconds=timeout_seconds,
    )
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@system_account_app.command("trade-acquire")
def account_trade_acquire(
    ctx: typer.Context,
    account: str = typer.Argument(..., help="Account id or broker.account key"),
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str = typer.Option(DEFAULT_SYSTEM_LAUNCH_ID, "--launch-id"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for the system runtime response."),
    timeout_seconds: float = typer.Option(5.0, "--timeout"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _system_account_command(
        "account.trade-acquire",
        account=account,
        root=root,
        launch_id=launch_id,
        wait=wait,
        timeout_seconds=timeout_seconds,
    )
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@system_account_app.command("trade-release")
def account_trade_release(
    ctx: typer.Context,
    account: str = typer.Argument(..., help="Account id or broker.account key"),
    root: Path | None = typer.Option(None, "--root"),
    launch_id: str = typer.Option(DEFAULT_SYSTEM_LAUNCH_ID, "--launch-id"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for the system runtime response."),
    timeout_seconds: float = typer.Option(5.0, "--timeout"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _system_account_command(
        "account.trade-release",
        account=account,
        root=root,
        launch_id=launch_id,
        wait=wait,
        timeout_seconds=timeout_seconds,
    )
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


def _system_account_command(
    kind: str,
    *,
    account: str | None,
    root: Path | None,
    launch_id: str,
    wait: bool,
    timeout_seconds: float,
) -> dict[str, object]:
    try:
        return _RUNS.system_command(
            kind=kind,
            payload={"account": account} if account is not None else {},
            root=root,
            launch_id=launch_id,
            wait=wait,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


__all__ = ["system_app", "system_account_app"]
