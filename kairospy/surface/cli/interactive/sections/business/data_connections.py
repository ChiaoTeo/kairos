"""Interactive Workspace data-provider connections."""

from __future__ import annotations

import typer

from kairospy.application.reference import ReferenceProviderConfigurationApplication
from kairospy.application.config import ConfigurationReferenceApplication

from ...models import (
    CommandExecution,
    GuidedCommand,
    InteractiveContext,
    ShellAction,
    ShellControl,
)

ROOT = ("resources", "data")


def print_menu(context: InteractiveContext) -> None:
    values = _connections(context)
    if len(context.shell_path) == 3 and context.shell_path[:2] == ROOT:
        connection_id = context.shell_path[2]
        value = next(
            (
                item
                for item in values
                if str(item.get("connection_id")) == connection_id
            ),
            {"connection_id": connection_id},
        )
        _print_detail(context, value)
        typer.echo(
            "\n建议操作：\n"
            "  1. 测试连接\n"
            "  2. 修改配置\n"
            "  3. 安全与高级信息\n"
            + ("  4. 启用\n" if value.get("enabled") is False else "  4. 禁用\n")
            + "  5. 删除"
        )
        return
    typer.echo("市场数据：")
    if not values:
        typer.echo("  还没有市场数据连接。")
    for index, value in enumerate(values, start=1):
        status = _status_label(str(value.get("verification_status") or "pending"))
        typer.echo(f"  {index}. Massive 美股数据 · {status}")
    typer.echo(f"  {len(values) + 1}. 添加市场数据")


def print_help(context: InteractiveContext) -> None:
    del context
    typer.echo("输入序号打开连接或添加市场数据；b 返回运行准备。")


def handle(context: InteractiveContext, parts: tuple[str, ...]) -> ShellAction:
    if len(parts) != 1:
        return None
    key = parts[0]
    if len(context.shell_path) == 3 and context.shell_path[:2] == ROOT:
        connection_id = context.shell_path[2]
        if key in {"1", "t", "test", "verify"}:
            return GuidedCommand(
                ("config", "data", "test", connection_id),
                "实际读取固定、低成本样本；无远端写入",
                dangerous=True,
            )
        if key in {"2", "edit", "setup"}:
            return GuidedCommand(
                ("config", "data", "setup"),
                "修改 Massive 市场数据连接",
                execution=CommandExecution.INTERACTIVE,
                show_command=False,
            )
        if key in {"3", "advanced", "references", "uses"}:
            _print_advanced(context, value)
            return ShellControl.HANDLED
        if key in {"4", "disable", "enable"}:
            action = (
                "enable"
                if key == "enable" or (key == "4" and value.get("enabled") is False)
                else "disable"
            )
            return GuidedCommand(
                ("config", "data", action, connection_id),
                f"{'启用' if action == 'enable' else '禁用'}市场数据连接 {connection_id}",
                dangerous=action == "disable",
            )
        if key in {"5", "delete"}:
            return GuidedCommand(
                ("config", "data", "delete", connection_id),
                f"删除未被 Launch 引用的数据连接 {connection_id}",
                dangerous=True,
            )
        return None
    connections = _connections(context)
    if key in {"n", "new", "setup", str(len(connections) + 1)}:
        return GuidedCommand(
            ("config", "data", "setup"),
            "添加 Massive 市场数据连接",
            execution=CommandExecution.INTERACTIVE,
            show_command=False,
        )
    if key in {"list", "ls"}:
        print_menu(context)
        return ShellControl.HANDLED
    if key.isdigit():
        index = int(key)
        if not 1 <= index <= len(connections):
            typer.echo(f"找不到数据连接序号：{key}")
            return ShellControl.HANDLED
        context.shell_path = (*ROOT, str(connections[index - 1]["connection_id"]))
        return ShellControl.HANDLED
    return None


def _connections(context: InteractiveContext) -> list[dict[str, object]]:
    if context.owner is None:
        return []
    try:
        return ReferenceProviderConfigurationApplication(context.owner).list()
    except (OSError, ValueError):
        return []


def _print_detail(context: InteractiveContext, value: dict[str, object]) -> None:
    typer.echo(
        "\n".join(
            (
                "Massive 美股数据",
                f"状态：{_status_label(str(value.get('verification_status') or 'pending'))}",
                "用途：股票信息和历史行情",
                f"凭据：{'已配置' if value.get('credential_id') else '缺少凭据'}",
                f"最近测试：{value.get('last_tested_at') or '尚未测试'}",
            )
        )
    )


def _print_advanced(context: InteractiveContext, value: dict[str, object]) -> None:
    references = (
        ConfigurationReferenceApplication(context.owner).data_provider_references(
            str(value.get("connection_id") or "")
        )
        if context.owner is not None
        else []
    )
    typer.echo(
        "安全与高级信息：\n"
        f"  连接 ID：{value.get('connection_id')}\n"
        f"  Endpoint：{value.get('endpoint') or '-'}\n"
        f"  认证资料：{value.get('credential_id') or '-'}（值不显示）\n"
        f"  配置版本：{_hash_label(value.get('current_configuration_hash'))}\n"
        f"  测试版本：{_hash_label(value.get('tested_configuration_hash'))}\n"
        f"  已测试：{', '.join(str(item) for item in value.get('tested') or ()) or '-'}\n"
        f"  未测试：{', '.join(str(item) for item in value.get('not_tested') or ()) or '-'}\n"
        "  运行方案引用："
        + (
            "；".join(f"{item['source']}:{item['location']}" for item in references)
            or "无"
        )
    )


def _status_label(status: str) -> str:
    return {
        "verified": "可用",
        "pending": "需要测试",
        "retest_required": "配置已变化",
        "failed": "连接失败",
        "disabled": "已禁用",
    }.get(status, status)


def _hash_label(value: object) -> str:
    return str(value)[:12] if isinstance(value, str) and value else "-"
