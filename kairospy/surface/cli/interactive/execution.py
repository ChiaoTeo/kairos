"""Execution mechanics for guided commands."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import shlex
import sys

import click
import typer

from kairospy.surface.cli.activity import TerminalActivity

from .context import refresh_context
from .models import CommandExecution, ExecuteCommand, GuidedCommand, InteractiveContext


def execute_guided_command(
    context: InteractiveContext,
    command: GuidedCommand,
    *,
    execute: ExecuteCommand,
    yes: bool,
) -> None:
    argv = with_workspace(command, context.workspace_arg)
    display = display_command(argv)
    typer.echo()
    typer.echo(f"── {command.summary} ──")
    if command.show_command:
        typer.echo(f"准备执行：{display}")
        typer.echo(f"用途：{command.summary}")
    try:
        if command.dangerous and not yes:
            typer.echo(
                command.confirmation or "此操作会修改配置、调用外部服务或影响运行。"
            )
            if not typer.confirm("确认继续吗？", default=True):
                _record_cancellation(context, display)
                return
    except (KeyboardInterrupt, click.Abort):
        _record_cancellation(context, display)
        return
    context.last_command = display
    try:
        status = _execute_with_activity(
            execute,
            argv,
            label=command.summary,
            enabled=command.execution is CommandExecution.ACTIVITY,
        )
    except (KeyboardInterrupt, click.Abort):
        _record_cancellation(context, display)
        return
    if status == 130:
        _record_cancellation(context, display)
        return
    context.last_status = status
    result = "完成" if status == 0 else "失败"
    typer.echo()
    typer.echo(f"── {result} · status={status} ──")
    typer.echo()
    refresh_context(context)


def _record_cancellation(
    context: InteractiveContext, display: str, *, saved: bool = False
) -> None:
    typer.echo("已取消当前操作。" if saved else "已取消当前操作，未保存任何修改。")
    typer.echo("── 已取消 ──")
    typer.echo()
    context.last_command = display
    context.last_status = 0


def _execute_with_activity(
    execute: ExecuteCommand,
    argv: Sequence[str],
    *,
    label: str,
    enabled: bool,
) -> int:
    output = sys.stdout
    activity = TerminalActivity(label, output)
    if not enabled or not activity.enabled:
        return execute(argv)

    captured = StringIO()
    activity.start()
    try:
        with redirect_stdout(captured):
            status = execute(argv)
    except BaseException:
        activity.finish(succeeded=False)
        output.write(captured.getvalue())
        output.flush()
        raise
    activity.finish(succeeded=status == 0)
    output.write(captured.getvalue())
    output.flush()
    return status


def with_workspace(command: GuidedCommand, workspace: Path | None) -> tuple[str, ...]:
    argv = command.argv
    if not command.needs_workspace or workspace is None:
        return argv
    if "--workspace" in argv or any(item.startswith("--workspace=") for item in argv):
        return argv
    return (*argv, "--workspace", str(workspace))


def display_command(argv: Sequence[str]) -> str:
    return "kairos " + shlex.join(tuple(argv))
