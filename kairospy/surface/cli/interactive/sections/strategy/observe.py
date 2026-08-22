"""Interactive runtime observation and diagnosis section."""

from __future__ import annotations

import shlex

import typer

from kairospy.surface.console.models import recommended_action

from ...models import GuidedCommand, InteractiveContext
from .launch import launch_ids


def print_menu(context: InteractiveContext) -> None:
    del context
    typer.echo("诊断与观测：\n  1. 打开观测台\n  2. 输出一次快照\n  3. 推荐下一步诊断动作")


def print_help(context: InteractiveContext) -> None:
    del context
    typer.echo("可用命令：open/once/diagnose")


def handle(
    context: InteractiveContext, parts: tuple[str, ...]
) -> GuidedCommand | None:
    if len(parts) != 1:
        return None
    if parts[0] in {"1", "open", "observe"}:
        return GuidedCommand(("observe",), "打开项目观测台", streaming=True)
    if parts[0] in {"2", "once"}:
        return GuidedCommand(("observe", "--once"), "输出一次项目观察快照")
    if parts[0] in {"3", "diagnose", "doctor"}:
        return diagnose(context)
    return None


def diagnose(context: InteractiveContext) -> GuidedCommand:
    owner = context.owner
    snapshot = context.snapshot
    if owner is None:
        return GuidedCommand(
            ("project", "init"),
            "创建或初始化 Kairos 项目",
            dangerous=True,
            needs_workspace=False,
        )
    if snapshot is None or snapshot.error:
        return GuidedCommand(("project", "doctor"), "检查项目 readiness")
    if not launch_ids(owner, snapshot):
        return GuidedCommand(("project", "doctor"), "检查项目是否缺少 launch 配置")
    suggested = recommended_action(snapshot)
    typer.echo(f"我建议先运行：{suggested}")
    if typer.confirm("使用这条建议命令吗？", default=True):
        return GuidedCommand(tuple(shlex.split(suggested)[1:]), "执行推荐排障命令")
    return GuidedCommand(("system", "doctor"), "检查系统运行资源")


def choose() -> GuidedCommand:
    return GuidedCommand(("observe",), "打开项目观测台", streaming=True)
