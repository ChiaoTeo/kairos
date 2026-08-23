"""Interactive strategy Launch section."""

from __future__ import annotations

from typing import Any

from prettytable import PrettyTable
import typer

from kairospy.application.launch.application import (
    LaunchRegistryApplication,
    LaunchRuntimeApplication,
)
from kairospy.surface.console.models import ObserveSnapshot

from ...context import unique_launches
from ...models import GuidedCommand, InteractiveContext, ShellAction, ShellControl


_ACTIONS = {
    "start": (("start",), "启动策略运行", True, False),
    "status": (("status",), "查看策略和依赖状态", False, False),
    "logs": (("logs",), "查看策略日志", False, False),
    "attach": (("attach",), "跟随状态和最近输出", False, True),
    "wait": (("wait",), "等待回测完成并读取报告", False, False),
    "stop": (("stop",), "停止策略并释放运行资源", True, False),
    "validate": (("diagnose", "validate"), "校验 launch 配置", False, False),
    "edit": (("edit",), "交互式编辑 launch 配置", True, False),
    "report": (("report",), "读取最近完成的报告", False, False),
    "restart": (("restart",), "重启策略运行", True, False),
}


def print_menu(context: InteractiveContext) -> None:
    if context.shell_path == ("launch",):
        typer.echo("策略运行：")
        print_list(context)
        typer.echo("输入序号选择并进入 launch；refresh 刷新列表。")
        return
    launch_id = context.selected_launch or context.shell_path[1]
    if _is_components_path(context.shell_path):
        typer.echo(
            "实例组件：\n  1. market\n  2. execution\n  3. reference\n"
            "  4. risk\n  5. capital\n  6. account 命令帮助"
        )
        return
    if _is_timeline_path(context.shell_path):
        typer.echo("实例时间线：\n  1. 列出记录\n  2. 导出记录")
        return
    if _is_instances_path(context.shell_path):
        typer.echo(f"{launch_id} 的运行实例：")
        _print_instance_list(context, launch_id)
        typer.echo("输入序号选择实例；current 选择唯一运行实例。")
        return
    if _is_instance_path(context.shell_path):
        instance_id = context.selected_launch_instance or context.shell_path[3]
        typer.echo(
            f"Launch Instance：{launch_id}/{instance_id}\n"
            "  1. 实例概览\n  2. 实例组件\n  3. 实例时间线"
        )
        return
    typer.echo(
        "\n".join(
            (
                f"当前 launch：{launch_id}",
                "  1. 概览",
                "  2. 启动",
                "  3. 查看状态",
                "  4. 查看日志",
                "  5. 跟随状态和输出",
                "  6. 等待回测报告",
                "  7. 停止",
                "  8. 校验配置",
                "  9. 编辑配置",
                "  10. 查看最近报告",
                "  11. 重启",
                "  12. 查看运行实例",
            )
        )
    )


def print_help(context: InteractiveContext) -> None:
    if context.shell_path == ("launch",):
        typer.echo("可用命令：<序号>/select/list/back/home/exit")
        return
    if _is_components_path(context.shell_path):
        typer.echo("可用命令：market/execution/reference/risk/capital/account")
        return
    if _is_timeline_path(context.shell_path):
        typer.echo("可用命令：list/export")
        return
    typer.echo(
        "可用命令：summary/start/status/logs/attach/wait/stop/restart/validate/"
        "edit/report/instances/current"
    )


def _prompt_menu(title: str, choices: tuple[tuple[str, str], ...]) -> str:
    typer.echo(title)
    for key, label in choices:
        typer.echo(f"  {key}. {label}")
    valid = {key for key, _label in choices}
    while True:
        value = typer.prompt("请输入序号", default=choices[0][0]).strip()
        if value in valid:
            return value
        typer.echo("这个选项不存在，请重新输入。")


def handle(
    context: InteractiveContext, parts: tuple[str, ...]
) -> ShellAction:
    if context.shell_path == ("launch",):
        ids = launch_ids(context.owner, context.snapshot)
        if len(parts) == 1 and parts[0].isdigit():
            index = int(parts[0])
            if not 1 <= index <= len(ids):
                typer.echo(f"找不到 launch 序号：{parts[0]}")
                return ShellControl.HANDLED
            launch_id = ids[index - 1]
            context.selected_launch = launch_id
            context.selected_launch_mode = None
            context.selected_launch_instance = None
            context.selected_account = None
            context.selected_account_provider = None
            context.selected_account_environment = None
            context.selected_account_segment = None
            context.selected_order = None
            context.selected_order_symbol = None
            context.shell_path = ("launch", launch_id)
            print_summary(context)
            return ShellControl.HANDLED
        if parts in {("select",), ("enter",)}:
            launch_id = prompt_launch_id(
                context.owner, context.snapshot, context.selected_launch
            )
            if launch_id in {"b", "back"}:
                return ShellControl.HANDLED
            context.selected_launch = launch_id
            context.selected_launch_mode = None
            context.selected_launch_instance = None
            context.selected_account = None
            context.selected_account_provider = None
            context.selected_account_environment = None
            context.selected_account_segment = None
            context.selected_order = None
            context.selected_order_symbol = None
            context.shell_path = ("launch", launch_id)
            print_summary(context)
            return ShellControl.HANDLED
        if parts in {("list",), ("ls",)}:
            print_list(context)
            return ShellControl.HANDLED
        return None

    if _is_components_path(context.shell_path):
        return _handle_components(context, parts)
    if _is_timeline_path(context.shell_path):
        return _handle_timeline(context, parts)
    if _is_instances_path(context.shell_path):
        return _handle_instances(context, parts)
    if _is_instance_path(context.shell_path):
        return _handle_instance(context, parts)

    launch_id = context.shell_path[1]
    context.selected_launch = launch_id
    if parts in {("1",), ("summary",), ("overview",)}:
        print_summary(context)
        return ShellControl.HANDLED
    if parts in {("12",), ("instances",)}:
        context.shell_path = ("launch", launch_id, "instances")
        _auto_select_only_instance(context)
        return ShellControl.HANDLED
    if parts == ("current",):
        return _enter_current_instance(context)
    aliases = {
        "2": "start", "3": "status", "4": "logs", "5": "attach",
        "6": "wait", "7": "stop", "8": "validate", "9": "edit",
        "10": "report", "11": "restart",
    }
    action = aliases.get(parts[0], parts[0]) if len(parts) == 1 else ""
    if action not in _ACTIONS:
        return None
    return build_command(launch_id, action)


def _handle_components(
    context: InteractiveContext, parts: tuple[str, ...]
) -> ShellAction:
    if len(parts) != 1:
        return None
    component = {
        "1": "market", "market": "market",
        "2": "execution", "execution": "execution",
        "3": "reference", "reference": "reference",
        "4": "risk", "risk": "risk",
        "5": "capital", "capital": "capital",
        "6": "account", "account": "account",
    }.get(parts[0])
    if component is None:
        return None
    launch_id = context.selected_launch or context.shell_path[1]
    instance_id = context.selected_launch_instance or context.shell_path[3]
    context.selected_launch = launch_id
    context.selected_launch_instance = instance_id
    if component == "market":
        from ..business import market
        context.shell_path = (*context.shell_path, "market")
        return ShellControl.HANDLED
    if component == "execution":
        context.shell_path = (*context.shell_path, "execution")
        return ShellControl.HANDLED
    if component == "account":
        return GuidedCommand(
            ("launch", "instance", "component", "account", "--help"),
            "查看 launch Account 组件入口",
        )
    return GuidedCommand(
        (
            "launch", "instance", "component", component, "status", launch_id,
            "--instance", instance_id, "--format", "text",
        ),
        f"查看 launch {component} 组件状态",
    )


def _handle_timeline(
    context: InteractiveContext, parts: tuple[str, ...]
) -> GuidedCommand | None:
    if len(parts) != 1:
        return None
    action = {"1": "list", "list": "list", "2": "export", "export": "export"}.get(parts[0])
    if action is None:
        return None
    launch_id = context.selected_launch or context.shell_path[1]
    instance_id = context.selected_launch_instance or context.shell_path[3]
    argv = ("launch", "instance", "timeline", action, launch_id, instance_id)
    if action == "list":
        limit = typer.prompt("limit", default="50").strip()
        argv = (*argv, "--limit", limit, "--format", "table")
    else:
        destination = typer.prompt(
            "导出文件", default=f"{launch_id}-{instance_id}-timeline.json"
        ).strip()
        argv = (*argv, "--destination", destination, "--format", "text")
    return GuidedCommand(
        argv,
        "查看 launch instance 时间线" if action == "list" else "导出 launch instance 时间线",
        dangerous=action == "export",
    )


def _handle_instances(
    context: InteractiveContext, parts: tuple[str, ...]
) -> ShellAction:
    if len(parts) != 1:
        return None
    if parts == ("current",):
        return _enter_current_instance(context)
    entries = _instance_entries(context)
    key = parts[0]
    if key.isdigit():
        index = int(key)
        if not 1 <= index <= len(entries):
            typer.echo(f"找不到 instance 序号：{key}")
            return ShellControl.HANDLED
        _enter_instance(context, entries[index - 1])
        return ShellControl.HANDLED
    matches = [entry for entry in entries if entry.get("instance_id") == key]
    if len(matches) == 1:
        _enter_instance(context, matches[0])
        return ShellControl.HANDLED
    if len(matches) > 1:
        typer.echo(f"instance {key} 存在于多个 mode，请使用序号选择。")
        return ShellControl.HANDLED
    return None


def _handle_instance(
    context: InteractiveContext, parts: tuple[str, ...]
) -> ShellAction:
    if len(parts) != 1:
        return None
    key = parts[0]
    if key in {"1", "summary", "overview"}:
        _print_instance_summary(context)
        return ShellControl.HANDLED
    if key in {"2", "components"}:
        context.shell_path = (*context.shell_path, "components")
        return ShellControl.HANDLED
    if key in {"3", "timeline"}:
        context.shell_path = (*context.shell_path, "timeline")
        return ShellControl.HANDLED
    return None


def _instance_entries(context: InteractiveContext) -> list[dict[str, Any]]:
    if context.owner is None:
        return []
    launch_id = context.selected_launch or context.shell_path[1]
    return LaunchRegistryApplication(context.owner).instances(launch_id)


def _print_instance_list(context: InteractiveContext, launch_id: str) -> None:
    entries = _instance_entries(context)
    if not entries:
        typer.echo("当前 launch 没有已注册 instance。")
        return
    table = PrettyTable(["序号", "instance", "mode", "state", "updated"])
    table.align = "l"
    for index, entry in enumerate(entries, start=1):
        table.add_row(
            [
                index,
                entry.get("instance_id", "-"),
                entry.get("mode", "-"),
                entry.get("state", "unknown"),
                entry.get("updated_at", "-"),
            ]
        )
    typer.echo(table)


def _auto_select_only_instance(context: InteractiveContext) -> None:
    entries = _instance_entries(context)
    if len(entries) == 1:
        _enter_instance(context, entries[0])


def _enter_current_instance(context: InteractiveContext) -> ShellControl:
    if context.owner is None:
        typer.echo("当前没有可解析的 workspace。")
        return ShellControl.HANDLED
    launch_id = context.selected_launch or context.shell_path[1]
    try:
        entry = LaunchRuntimeApplication(context.owner).running_instance(launch_id)
    except Exception as error:
        typer.echo(f"无法解析 current instance：{error}")
        return ShellControl.HANDLED
    if entry is None:
        typer.echo("当前 launch 没有唯一运行中的 instance。")
        return ShellControl.HANDLED
    _enter_instance(context, entry)
    return ShellControl.HANDLED


def _enter_instance(context: InteractiveContext, entry: dict[str, Any]) -> None:
    launch_id = context.selected_launch or context.shell_path[1]
    instance_id = str(entry.get("instance_id") or "")
    mode = str(entry.get("mode") or "")
    if not instance_id or not mode:
        typer.echo("instance registry 记录缺少 instance_id 或 mode。")
        return
    context.selected_launch = launch_id
    context.selected_launch_instance = instance_id
    context.selected_launch_mode = mode
    context.shell_path = ("launch", launch_id, "instances", instance_id)
    _print_instance_summary(context)


def _print_instance_summary(context: InteractiveContext) -> None:
    launch_id = context.selected_launch or context.shell_path[1]
    instance_id = context.selected_launch_instance or context.shell_path[3]
    entry = next(
        (
            value
            for value in _instance_entries(context)
            if value.get("instance_id") == instance_id
            and (
                context.selected_launch_mode is None
                or value.get("mode") == context.selected_launch_mode
            )
        ),
        {},
    )
    table = PrettyTable(["Instance 上下文", "值"])
    table.align = "l"
    table.add_row(["launch", launch_id])
    table.add_row(["instance", instance_id])
    table.add_row(["mode", entry.get("mode") or context.selected_launch_mode or "-"])
    table.add_row(["state", entry.get("state", "unknown")])
    typer.echo(table)


def _is_instances_path(path: tuple[str, ...]) -> bool:
    return len(path) == 3 and path[0] == "launch" and path[2] == "instances"


def _is_instance_path(path: tuple[str, ...]) -> bool:
    return len(path) == 4 and path[0] == "launch" and path[2] == "instances"


def _is_components_path(path: tuple[str, ...]) -> bool:
    return len(path) == 5 and path[0] == "launch" and path[2] == "instances" and path[4] == "components"


def _is_timeline_path(path: tuple[str, ...]) -> bool:
    return len(path) == 5 and path[0] == "launch" and path[2] == "instances" and path[4] == "timeline"


def print_list(context: InteractiveContext) -> None:
    ids = launch_ids(context.owner, context.snapshot)
    if not ids:
        typer.echo("当前 workspace 没有可用 launch。")
        return
    records = {
        str(record.get("launch_id")): record
        for record in (
            unique_launches(context.snapshot) if context.snapshot is not None else ()
        )
    }
    table = PrettyTable(["序号", "launch", "mode", "state", "instance"])
    table.align = "l"
    for index, launch_id in enumerate(ids, start=1):
        record = records.get(launch_id, {})
        table.add_row(
            [
                index,
                launch_id,
                record.get("mode", "-"),
                record.get("state", "not_started"),
                record.get("instance_id", "-"),
            ]
        )
    typer.echo(table)


def print_summary(context: InteractiveContext) -> None:
    launch_id = context.selected_launch
    if launch_id is None:
        typer.echo("请先选择 launch。")
        return
    record = {}
    if context.snapshot is not None:
        record = next(
            (
                value
                for value in unique_launches(context.snapshot)
                if value.get("launch_id") == launch_id
            ),
            {},
        )
    config = (
        context.owner.paths.launch_config(launch_id)
        if context.owner is not None
        else None
    )
    table = PrettyTable(["launch 上下文", "值"])
    table.align = "l"
    table.add_row(["launch", launch_id])
    table.add_row(["mode", record.get("mode", "-")])
    table.add_row(["state", record.get("state", "not_started")])
    table.add_row(["instance", record.get("instance_id", "-")])
    table.add_row(["config", str(config) if config is not None else "-"])
    typer.echo(table)


def choose(context: InteractiveContext) -> GuidedCommand:
    launch_id = prompt_launch_id(
        context.owner, context.snapshot, context.selected_launch
    )
    context.selected_launch = launch_id
    action = _prompt_menu(
        "你想对这个策略做什么？",
        (
            ("1", "启动"),
            ("2", "查看状态"),
            ("3", "查看日志"),
            ("4", "跟随状态和输出"),
            ("5", "等待回测报告"),
            ("6", "停止"),
            ("7", "校验配置"),
            ("8", "编辑配置"),
            ("9", "查看最近报告"),
        ),
    )
    action_name = {
        "1": "start", "2": "status", "3": "logs", "4": "attach",
        "5": "wait", "6": "stop", "7": "validate", "8": "edit",
        "9": "report",
    }[action]
    return build_command(launch_id, action_name)


def build_command(launch_id: str, action: str) -> GuidedCommand:
    argv, summary, dangerous, streaming = _ACTIONS[action]
    command_argv = (
        ("launch", *argv)
        if "--help" in argv
        else ("launch", *argv, launch_id)
    )
    return GuidedCommand(
        command_argv,
        summary,
        dangerous=dangerous,
        streaming=streaming,
    )


def prompt_launch_id(
    owner, snapshot: ObserveSnapshot | None, selected_launch: str | None = None
) -> str:
    ids = launch_ids(owner, snapshot)
    if ids:
        typer.echo("可用 launch：")
        for index, launch_id in enumerate(ids, start=1):
            typer.echo(f"  {index}. {launch_id}")
        default = (
            str(ids.index(selected_launch) + 1)
            if selected_launch in ids
            else "1"
        )
        value = typer.prompt(
            "选择 launch 序号或直接输入 launch id", default=default
        ).strip()
        if value.isdigit() and 1 <= int(value) <= len(ids):
            return ids[int(value) - 1]
        return value
    return typer.prompt("launch id", default="demo-backtest").strip()


def launch_ids(owner, snapshot: ObserveSnapshot | None) -> tuple[str, ...]:
    values: list[str] = []
    if snapshot is not None:
        for item in snapshot.launches:
            launch_id = str(item.get("launch_id") or "").strip()
            if launch_id and launch_id not in values:
                values.append(launch_id)
    if owner is not None:
        config_dir = owner.paths.config / "launches"
        if config_dir.is_dir():
            for path in sorted(config_dir.glob("*.toml")):
                if path.stem not in values:
                    values.append(path.stem)
        try:
            for item in LaunchRegistryApplication(owner).list():
                launch_id = str(item.get("launch_id") or "").strip()
                if launch_id and launch_id not in values:
                    values.append(launch_id)
        except Exception:
            pass
    return tuple(values)
