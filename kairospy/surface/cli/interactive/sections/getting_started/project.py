"""Interactive Project and workspace onboarding section."""

from __future__ import annotations

from pathlib import Path

import typer

from ...models import GuidedCommand, InteractiveContext


def print_menu(context: InteractiveContext) -> None:
    del context
    typer.echo(
        "项目：\n  1. 创建项目\n  2. 查看项目状态\n  3. 安装示例模板\n  4. 运行项目诊断"
    )


def print_help(context: InteractiveContext) -> None:
    del context
    typer.echo("可用命令：init/status/scaffold/doctor")


def handle(
    context: InteractiveContext, parts: tuple[str, ...]
) -> GuidedCommand | None:
    del context
    if len(parts) != 1:
        return None
    key = parts[0]
    if key in {"1", "init"}:
        return choose()
    if key in {"2", "status"}:
        return GuidedCommand(("project", "status", "--format", "text"), "查看项目状态")
    if key in {"3", "scaffold"}:
        template = typer.prompt("模板", default="backtest").strip() or "backtest"
        return GuidedCommand(
            ("project", "scaffold", "--template", template, "--format", "text"),
            "安装项目示例模板",
            dangerous=True,
        )
    if key in {"4", "doctor"}:
        return GuidedCommand(("project", "doctor", "--format", "text"), "检查项目 readiness")
    return None


def choose() -> GuidedCommand:
    root = typer.prompt("项目目录", default="my-project").strip()
    default_id = Path(root).expanduser().name or "my-project"
    workspace_id = typer.prompt("项目名 / workspace id", default=default_id).strip()
    template = typer.prompt("模板", default="backtest").strip() or "backtest"
    return GuidedCommand(
        ("project", "init", root, "--id", workspace_id, "--template", template),
        "创建项目；backtest 模板会生成离线可跑的示例策略",
        dangerous=True,
        needs_workspace=False,
    )
