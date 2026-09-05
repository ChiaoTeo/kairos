"""Stateless parsing and rendering helpers for the Workbench command line."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import Any

from rich.console import RenderableType
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table
from rich.text import Text

from kairospy.surface.presentation import redact_cli_arguments, redact_value

from .activity import ActivityKind
from .flows.operations.observe_view import observe_renderable
from .navigation import Section, belongs_to
from .results import ResultKind


_COMMAND_ALIASES = {"b": "back"}


def parse_command(value: str) -> tuple[str, tuple[str, ...]]:
    stripped = value.strip().removeprefix("/")
    try:
        parts = shlex.split(stripped)
    except ValueError:
        return "", ()
    if not parts:
        return "help", ()
    command = parts[0].lower()
    return _COMMAND_ALIASES.get(command, command), tuple(parts[1:])


def value_or_unknown(arguments: tuple[str, ...]) -> str:
    return " ".join(arguments) or "<无法解析>"


def activity_kind(kind: ResultKind) -> ActivityKind:
    if kind in {
        ResultKind.OBSERVE,
        ResultKind.MARKET,
        ResultKind.MARKET_ROUTES,
        ResultKind.MARKET_OBSERVATION,
        ResultKind.MARKET_DATASETS,
        ResultKind.MARKET_CATALOG_SETUP,
        ResultKind.REFERENCE_RECORDS,
        ResultKind.REFERENCE_RELATED,
        ResultKind.REFERENCE_STATUS,
        ResultKind.RESOURCES_SUMMARY,
        ResultKind.RESOURCE_LIST,
        ResultKind.OPERATIONS_SERVICES,
        ResultKind.OPERATIONS_OVERVIEW,
        ResultKind.STRATEGY_LAUNCHES,
        ResultKind.STRATEGY_INSTANCES,
        ResultKind.STRATEGY_COMPONENTS,
        ResultKind.STRATEGY_INSTANCE,
        ResultKind.STRATEGY_TIMELINE,
    }:
        return ActivityKind.QUERY
    if kind is ResultKind.STRATEGY_TIMELINE_EXPORT:
        return ActivityKind.ARTIFACT
    return ActivityKind.OPERATION


def shell_result_body(kind: ResultKind, result: Any) -> RenderableType:
    """Render the small set of non-product operations owned by the shell."""

    if kind is ResultKind.OBSERVE:
        return (
            Text("当前没有可用的系统观察结果。", style="dim")
            if result is None
            else observe_renderable(result)
        )
    if kind is ResultKind.KAIROS_COMMAND:
        return Panel(
            Pretty(_redact_result(result), expand_all=True), title="kairos 命令结果"
        )
    return Panel(str(result), title="完成", border_style="green")


def _redact_result(value: Any) -> Any:
    """Redact nested shell results before they become a visible Rich renderable."""

    if isinstance(value, Mapping):
        redacted = redact_value(value)
        command = value.get("command")
        if isinstance(command, (list, tuple)):
            redacted["command"] = list(
                redact_cli_arguments(tuple(str(part) for part in command))
            )
        return redacted
    return redact_value(value)


def help_table(context: tuple[str, ...] = ()) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    table.add_row("编号 / ↑↓ + Enter", "执行当前上方列出的操作")
    table.add_row("普通文本", "只在界面明确要求输入参数时填写")
    table.add_row("/命令", "执行明确的 Workbench 高级命令")
    table.add_row("kairos <命令>", "显式执行高级 CLI Application 命令")
    table.add_row(
        "/b, /back",
        "单一目标直接返回；多个目标时在交互区选择层级",
    )
    table.add_row("/home", "返回首页")
    table.add_row("p /project", "进入全局项目管理")
    table.add_row("/exit", "退出 Kairos Workbench")
    table.add_row("/observe", "打开当前项目的运行中心")
    table.add_row("/market [代码]", "搜索有效市场标的；省略代码时进入引导")
    table.add_row("/c", "打开我的实时行情")
    if belongs_to(context, Section.MARKET):
        table.add_row("/r", "回放本地 JSONL 行情")
        table.add_row("/d", "诊断市场定义和 Reference 映射")
        table.add_row("/a", "输入完整 Market ID")
    table.add_row("/clear", "清空当前输出显示")
    table.add_row("/panel [模式]", "切换或指定交互区默认、收起状态")
    table.add_row(
        "/theme [名称]",
        "选择或切换 Tokyo Night、Catppuccin、Nord、Gruvbox、Everforest、Dracula",
    )
    table.add_row("/transcript", "显示当前 Agent 可读会话记录的路径")
    table.add_row("/copy", "复制当前页完整输出，可直接粘贴给 Agent")
    table.add_row("/copy-interaction", "只复制当前交互区（也可输入 /copy interaction）")
    table.add_row("/copy 12-18", "按稳定 Activity 编号跨页复制")
    table.add_row("/copy-selected", "复制内容区中用 Space 选中的 Activity")
    table.add_row("/copy-history", "只复制当前会话的活动记录")
    table.add_row("/goto 12", "定位并聚焦 Activity A012")
    table.add_row("/up 20 /down 20", "按指定显示行数滚动内容区")
    table.add_row("/bottom", "回到最新活动并恢复自动跟随")
    table.add_row("/y /n", "继续或取消等待中的步骤")
    table.add_row("PgUp / PgDn", "翻阅内容区；Mac 可使用 Fn+↑ / Fn+↓")
    table.add_row("Ctrl+End / /bottom", "回到底部并继续跟随新输出")
    table.add_row("Alt/⌥+PgUp/PgDn", "滚动内容超出高度上限的交互区")
    table.add_row("Ctrl+O", "收起或恢复交互区")
    table.add_row("Tab / Shift+Tab", "在输入、交互选项和内容区之间切换焦点")
    table.add_row("内容区 ↑↓ / Space / C", "定位、多选并复制 Activity")
    table.add_row("交互区 ⌘C / Ctrl+Shift+C", "只复制当前交互内容")
    table.add_row("↑ / ↓, Enter", "在聚焦的交互区移动并执行选中项")
    table.add_row("/help", "显示这份帮助")
    return table


def running_status(kind: ResultKind) -> str:
    return {
        ResultKind.OBSERVE: "正在读取系统状态…",
        ResultKind.MARKET: "正在搜索市场标的…",
        ResultKind.MARKET_CATALOG_SETUP: "正在检查标的目录准备条件…",
        ResultKind.MARKET_CATALOG_PREPARE: "正在准备标的目录…",
    }.get(kind, "正在执行…")
