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
    data_connections,
    integration,
    market,
    notifications,
    models,
    order,
    reference,
    risk,
)
from .sections.business import execution_component
from .sections.getting_started import home, project, resources
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
    while True:
        _print_menu(context)
        if context.shell_path:
            typer.echo("  b. 返回上一级")
        try:
            prompt = f"{prompt_path(context)}> " if context.shell_path else "›"
            line = input(f"{_prompt_label(context)} {prompt} ").strip()
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
        if line in {"continue", "resume"} and _resume_launch_draft(context):
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


def _prompt_label(context: InteractiveContext) -> str:
    """Return a product-facing breadcrumb while keeping shell paths internal."""

    labels = {
        "market": "市场行情",
        "reference": "市场目录",
        "strategy": "策略运行",
        "launch": "策略运行",
        "observe": "诊断与观测",
        "trade": "交易管理",
        "resources": "运行资源",
        "accounts": "交易账户",
        "models": "模型连接",
        "notifications": "通知渠道",
        "data-research": "数据与研究",
        "data": "数据",
        "research": "研究",
        "operations": "系统与配置",
        "system": "系统服务",
        "project": "项目工作区",
        "notifications": "通知",
        "config": "高级配置",
        "risk": "风险管理",
        "capital": "资金管理",
    }
    if not context.shell_path:
        return "首页"
    parts = tuple(labels.get(part, part) for part in context.shell_path)
    return " / ".join(("首页", *parts))


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
    if path[:2] in {("trade", "accounts"), ("resources", "accounts")}:
        if len(path) >= 4 and path[3] == "orders":
            return order.handle(context, parts)
        return account.handle(context, parts)
    if path == ("resources",):
        return resources.handle(context, parts)
    if path[:2] == ("resources", "models"):
        return models.handle(context, parts)
    if path[:2] == ("resources", "notifications"):
        return notifications.handle(context, parts)
    if path[:2] == ("resources", "data"):
        return data_connections.handle(context, parts)
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


def _resume_launch_draft(context: InteractiveContext) -> bool:
    if context.owner is None or context.selected_launch is None:
        return False
    from kairospy.application.launch.application import LaunchConfigurationApplication
    from .sections.strategy import launch

    application = LaunchConfigurationApplication()
    return_point = application.draft_return(
        context.owner.paths.root, context.selected_launch
    )
    if return_point is None:
        return False
    resource = str(return_point.get("resource") or "")
    if not _launch_return_resource_ready(context, resource):
        typer.echo("对应运行资源尚未完成手动验证；返回点已保留。")
        return True
    application.clear_draft_return(context.owner.paths.root, context.selected_launch)
    context.shell_path = ("launch", context.selected_launch)
    typer.echo(f"继续编辑 Launch {context.selected_launch}。")
    launch.print_summary(context)
    return True


def _launch_return_resource_ready(context: InteractiveContext, resource: str) -> bool:
    if context.owner is None:
        return False
    if resource == "accounts":
        from kairospy.application.account import AccountConfigurationApplication

        values = AccountConfigurationApplication(context.owner).list()
    elif resource == "data":
        from kairospy.application.reference import (
            ReferenceProviderConfigurationApplication,
        )

        values = ReferenceProviderConfigurationApplication(context.owner).list()
    elif resource == "models":
        from kairospy.application.agent import AgentResourceApplication

        values = AgentResourceApplication(context.owner).model_connections()
    elif resource == "notifications":
        from kairospy.application.notification import NotificationAdminApplication

        values = NotificationAdminApplication(context.owner).list()
    else:
        return False
    return any(value.get("verification_status") == "verified" for value in values)


def _print_menu(context: InteractiveContext) -> None:
    module = _section_module(context)
    module.print_menu(context)


def _print_help(context: InteractiveContext) -> None:
    module = _section_module(context)
    module.print_help(context)


def _print_summary(context: InteractiveContext) -> None:
    path = context.shell_path
    if len(path) >= 3 and path[:2] in {
        ("trade", "accounts"),
        ("resources", "accounts"),
    }:
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
    print_context(context)


def _section_module(context: InteractiveContext):
    path = context.shell_path
    if not path:
        return home
    section = next(iter(path))
    if home.is_group_path(path):
        return home
    if path[:2] in {("trade", "accounts"), ("resources", "accounts")}:
        if len(path) >= 4 and path[3] == "orders":
            return order
        return account
    if path == ("resources",):
        return resources
    if path[:2] == ("resources", "models"):
        return models
    if path[:2] == ("resources", "notifications"):
        return notifications
    if path[:2] == ("resources", "data"):
        return data_connections
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
