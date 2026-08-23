"""Interactive shell lifecycle and explicit product-section dispatch."""

from __future__ import annotations

from pathlib import Path
import shlex

import typer

from .context import create_context, go_back, go_home, print_context, refresh_context
from .execution import display_command, execute_guided_command, with_workspace
from .models import (
    ExecuteCommand,
    GuidedCommand,
    InteractiveContext,
    ShellAction,
    ShellControl,
)
from .sections.business import (
    account,
    capital,
    integration,
    market,
    notifications,
    order,
    reference,
    risk,
)
from .sections.business import execution_component
from .sections.getting_started import home, project
from .sections.research_data import data, research
from .sections.strategy import launch, observe
from .sections.system import config, runtime


def run_interactive(
    *,
    workspace: Path | None,
    dry_run: bool,
    no_exec: bool,
    yes: bool,
    execute: ExecuteCommand,
) -> int:
    """Run the interactive Kairos operator shell."""

    typer.echo("Kairos 交互式操作")
    typer.echo("选择你想完成的事情，Kairos 会引导你完成下一步。")
    typer.echo()
    context = create_context(workspace)
    if dry_run or no_exec:
        return _run_one_shot_preview(context)
    return _run_shell(context, execute=execute, yes=yes)


def _run_one_shot_preview(context: InteractiveContext) -> int:
    _print_global_context(context)
    command = home.choose(context)
    argv = with_workspace(command, context.workspace_arg)
    typer.echo()
    typer.echo(f"准备执行：{display_command(argv)}")
    typer.echo(f"用途：{command.summary}")
    typer.echo("已开启 dry-run/no-exec，只展示命令，不执行。")
    return 0


def _run_shell(
    context: InteractiveContext, *, execute: ExecuteCommand, yes: bool
) -> int:
    _print_global_context(context)
    typer.echo("输入序号选择产品动作；也可以输入命令。exit 退出。")
    while True:
        _print_menu(context)
        if context.shell_path:
            typer.echo("  b. 返回上一级")
        try:
            line = input(f"{prompt_path(context)}> ").strip()
        except EOFError:
            typer.echo()
            return context.last_status or 0
        if not line:
            continue
        if line in {"exit", "quit", "q"}:
            return context.last_status or 0
        if line in {"help", "?"}:
            _print_help(context)
            continue
        if line == "summary":
            _print_summary(context)
            continue
        if line == "status" and not context.shell_path:
            _print_global_context(context)
            continue
        if line == "refresh":
            refresh_context(context)
            _print_global_context(context)
            continue
        if line in {"home", "/"}:
            go_home(context)
            continue
        if line in {"back", "b"}:
            go_back(context)
            continue
        command = shell_command(context, line)
        if command is ShellControl.HANDLED:
            continue
        if command is None:
            typer.echo("无法识别这个命令。输入 help 查看当前上下文可用动作。")
            continue
        execute_guided_command(context, command, execute=execute, yes=yes)


def prompt_path(context: InteractiveContext) -> str:
    return "/" + "/".join(context.shell_path)


def shell_command(context: InteractiveContext, line: str) -> ShellAction:
    try:
        parts = tuple(shlex.split(line))
    except ValueError as error:
        typer.echo(f"命令解析失败：{error}")
        return None
    if not parts:
        return None
    path = context.shell_path
    if not path:
        return home.handle(context, parts)
    section = path[0]
    if home.is_group_path(path):
        return home.handle(context, parts)
    if path[:2] == ("trade", "accounts"):
        if len(path) >= 4 and path[3] == "orders":
            return order.handle(context, parts)
        return account.handle(context, parts)
    if (
        len(path) >= 6
        and path[0] == "launch"
        and path[-2:] == ("components", "execution")
    ):
        return execution_component.handle(context, parts)
    if section == "launch" and len(path) >= 6 and path[-2:] == ("components", "market"):
        return market.handle(context, parts)
    if section == "launch":
        return launch.handle(context, parts)
    if section == "reference":
        return reference.handle(context, parts)
    if section == "market":
        return market.handle(context, parts)
    if section == "data":
        return data.handle(context, parts)
    if section == "research":
        return research.handle(context, parts)
    if section == "system":
        return runtime.handle(context, parts)
    if section == "project":
        return project.handle(context, parts)
    if section == "observe":
        return observe.handle(context, parts)
    if section == "risk":
        return risk.handle(context, parts)
    if section == "capital":
        return capital.handle(context, parts)
    if section == "integration":
        return integration.handle(context, parts)
    if section == "notifications":
        return notifications.handle(context, parts)
    if section == "config":
        return config.handle(context, parts)
    return None


def _print_menu(context: InteractiveContext) -> None:
    module = _section_module(context)
    module.print_menu(context)


def _print_help(context: InteractiveContext) -> None:
    module = _section_module(context)
    module.print_help(context)


def _print_summary(context: InteractiveContext) -> None:
    path = context.shell_path
    if len(path) >= 3 and path[:2] == ("trade", "accounts"):
        account.print_summary(context)
        return
    if len(path) == 2 and path[0] == "launch":
        launch.print_summary(context)
        return
    if path and path[0] == "reference" and context.selected_reference is not None:
        reference.print_summary(context)
        return
    _print_global_context(context)


def _print_global_context(context: InteractiveContext) -> None:
    print_context(context, account.records(context))


def _section_module(context: InteractiveContext):
    path = context.shell_path
    if not path:
        return home
    section = next(iter(path))
    if home.is_group_path(path):
        return home
    if path[:2] == ("trade", "accounts"):
        if len(path) >= 4 and path[3] == "orders":
            return order
        return account
    if (
        len(path) >= 6
        and path[0] == "launch"
        and path[-2:] == ("components", "execution")
    ):
        return execution_component
    if path == ("system", "market") or (
        len(path) >= 6 and path[0] == "launch" and path[-2:] == ("components", "market")
    ):
        return market
    return {
        "launch": launch,
        "reference": reference,
        "market": market,
        "data": data,
        "research": research,
        "system": runtime,
        "project": project,
        "observe": observe,
        "risk": risk,
        "capital": capital,
        "integration": integration,
        "notifications": notifications,
        "config": config,
    }[section]
