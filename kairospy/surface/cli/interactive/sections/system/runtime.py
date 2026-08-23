"""Interactive workspace runtime lifecycle section."""

from __future__ import annotations

import typer

from ...models import GuidedCommand, InteractiveContext, ShellAction, ShellControl


_COMPONENTS = ("reference", "market")


def print_menu(context: InteractiveContext) -> None:
    if context.shell_path == ("system",):
        typer.echo(
            "\n".join(
                (
                    "系统服务：",
                    "  1. reference",
                    "  2. market",
                    "  3. 查看 Workspace 服务状态",
                    "  4. 运行 system doctor",
                    "  5. 修复 stale 运行资源",
                )
            )
        )
        return
    if context.shell_path == ("system", "market"):
        from ..business import market

        market.print_menu(context)
        return
    component = context.shell_path[-1]
    typer.echo(
        "\n".join(
            (
                f"{component} 动作：",
                "  1. 查看状态",
                "  2. 启动",
                "  3. 停止",
                "  4. 重启",
                "  5. 查看日志",
                "  6. 查看运行资源",
            )
        )
    )


def print_help(context: InteractiveContext) -> None:
    if context.shell_path == ("system",):
        typer.echo("可用命令：reference/market/list/doctor/repair/back/home")
        return
    if context.shell_path == ("system", "market"):
        from ..business import market

        market.print_help(context)
        return
    typer.echo("可用命令：status/start/stop/restart/logs/inspect/back/home")


def handle(context: InteractiveContext, parts: tuple[str, ...]) -> ShellAction:
    if context.shell_path == ("system",):
        routes = {
            "1": "reference",
            "reference": "reference",
            "2": "market",
            "market": "market",
        }
        key = parts[0] if len(parts) == 1 else ""
        component = routes.get(key)
        if component is not None:
            context.shell_path = ("system", component)
            context.selected_service = component
            return ShellControl.HANDLED
        if parts in {("3",), ("list",), ("ls",), ("status",)}:
            return GuidedCommand(
                ("system", "list", "--format", "table"),
                "列出 workspace 系统服务状态",
            )
        if parts in {("4",), ("doctor",)}:
            return GuidedCommand(("system", "doctor"), "诊断 socket、健康文件和锁")
        if parts in {("5",), ("repair",)}:
            return GuidedCommand(
                ("system", "repair"), "清理确认 stale 的运行资源", dangerous=True
            )
        return None
    if context.shell_path == ("system", "market"):
        from ..business import market

        return market.handle(context, parts)
    return _handle_component(context, parts)


def _handle_component(
    context: InteractiveContext, parts: tuple[str, ...]
) -> GuidedCommand | None:
    component = context.shell_path[-1]
    if component not in _COMPONENTS or len(parts) != 1:
        return None
    context.selected_service = component
    action = {
        "1": "status",
        "status": "status",
        "2": "up",
        "start": "up",
        "3": "down",
        "stop": "down",
        "4": "restart",
        "restart": "restart",
        "5": "logs",
        "logs": "logs",
        "6": "inspect",
        "inspect": "inspect",
    }.get(parts[0])
    if action is None:
        return None
    summary = {
        "status": f"查看 {component} 状态",
        "up": f"启动 {component}",
        "down": f"停止 {component}",
        "restart": f"重启 {component}",
        "logs": f"查看 {component} 日志",
        "inspect": f"查看 {component} 运行资源",
    }[action]
    argv = ("system", action, "--component", component)
    if action not in {"logs"}:
        argv = (*argv, "--format", "text")
    return GuidedCommand(
        argv,
        summary,
        dangerous=action in {"up", "down", "restart"},
        streaming=action == "logs",
    )


def choose(context: InteractiveContext) -> GuidedCommand:
    typer.echo("你想维护哪个系统服务？")
    typer.echo("  1. reference\n  2. market\n  3. 查看所有系统服务")
    typer.echo("  4. 运行 system doctor\n  5. 修复 stale 运行资源")
    service = typer.prompt("请输入序号", default="1").strip()
    if service in {"3", "4", "5"}:
        context.shell_path = ("system",)
        command = handle(context, ({"3": "5", "4": "6", "5": "7"}[service],))
        if not isinstance(command, GuidedCommand):
            raise typer.BadParameter("未知系统服务动作")
        return command
    component = "reference" if service == "1" else "market"
    context.selected_service = component
    typer.echo(f"你想对 {component} 做什么？")
    typer.echo("  1. 查看状态\n  2. 启动\n  3. 停止\n  4. 重启\n  5. 查看日志")
    action = typer.prompt("请输入序号", default="1").strip()
    context.shell_path = ("system", component)
    command = _handle_component(context, (action,))
    if command is None:
        raise typer.BadParameter("未知系统服务动作")
    return command
