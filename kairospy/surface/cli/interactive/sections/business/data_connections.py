"""Interactive Workspace data-provider connections."""

from __future__ import annotations

import typer

from kairospy.application.reference import ReferenceProviderConfigurationApplication
from kairospy.application.config import ConfigurationReferenceApplication

from ...models import GuidedCommand, InteractiveContext, ShellAction, ShellControl

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
            "  t. 手动读取测试\n"
            "  edit. 修改 SecretRef/endpoint/capabilities\n"
            "  disable. 禁用连接\n"
            "  delete. 删除连接（有引用时默认拒绝）"
        )
        return
    typer.echo("数据连接：")
    if not values:
        typer.echo("  当前没有数据 Provider 连接。")
    for index, value in enumerate(values, start=1):
        status = _status_label(str(value.get("verification_status") or "pending"))
        capabilities = ", ".join(str(item) for item in value.get("capabilities") or ())
        typer.echo(
            f"  {index}. {value['connection_id']} · Massive · {status} · {capabilities}"
        )
    typer.echo(
        "  n. 添加或修改 Massive 数据连接\n"
        "  t. 手动测试 AAPL 标的读取与 SPY 小样本小时线"
    )


def print_help(context: InteractiveContext) -> None:
    del context
    typer.echo("可用命令：n/setup/list/t/test/back/home。")


def handle(context: InteractiveContext, parts: tuple[str, ...]) -> ShellAction:
    if len(parts) != 1:
        return None
    key = parts[0]
    if len(context.shell_path) == 3 and context.shell_path[:2] == ROOT:
        connection_id = context.shell_path[2]
        if key in {"t", "test", "verify"}:
            return GuidedCommand(
                ("config", "data", "test", connection_id),
                "实际读取固定、低成本样本；无远端写入",
                dangerous=True,
            )
        if key in {"edit", "setup"}:
            return GuidedCommand(
                ("config", "data", "setup"),
                "修改 Massive SecretRef 及共享 Reference/Market 能力",
                dangerous=True,
            )
        if key == "disable":
            return GuidedCommand(
                ("config", "data", "disable", connection_id),
                f"禁用数据连接 {connection_id}",
                dangerous=True,
            )
        if key == "delete":
            return GuidedCommand(
                ("config", "data", "delete", connection_id),
                f"删除未被 Launch 引用的数据连接 {connection_id}",
                dangerous=True,
            )
        if key in {"references", "uses"}:
            _print_detail(
                context,
                next(
                    (
                        item
                        for item in _connections(context)
                        if str(item.get("connection_id")) == connection_id
                    ),
                    {"connection_id": connection_id},
                ),
            )
            return ShellControl.HANDLED
        return None
    if key in {"n", "new", "setup"}:
        return GuidedCommand(
            ("config", "data", "setup"),
            "配置 Massive SecretRef 及共享 Reference/Market 能力",
            dangerous=True,
        )
    if key in {"t", "test", "verify"}:
        if not _connections(context):
            typer.echo("请先配置 Massive 数据连接，再执行手动读取测试。")
            return ShellControl.HANDLED
        return GuidedCommand(
            ("config", "data", "test", "massive"),
            "实际读取固定、低成本样本；无远端写入",
            dangerous=True,
        )
    if key in {"list", "ls"}:
        print_menu(context)
        return ShellControl.HANDLED
    if key.isdigit():
        connections = _connections(context)
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
    references = (
        ConfigurationReferenceApplication(context.owner).data_provider_references(
            str(value.get("connection_id") or "")
        )
        if context.owner is not None
        else []
    )
    typer.echo(
        "\n".join(
            (
                f"数据连接：{value.get('connection_id')}",
                f"状态：{_status_label(str(value.get('verification_status') or 'pending'))}",
                f"安全凭据：{value.get('credential_id')}（值不显示）",
                f"能力：{', '.join(str(item) for item in value.get('capabilities') or ())}",
                f"共享范围：{', '.join(str(item) for item in value.get('shared_by') or ())}",
                f"最近测试：{value.get('last_tested_at') or '-'}",
                f"当前配置版本：{_hash_label(value.get('current_configuration_hash'))} · 测试版本：{_hash_label(value.get('tested_configuration_hash'))}",
                f"已测试：{', '.join(str(item) for item in value.get('tested') or ()) or '-'}",
                f"未测试：{', '.join(str(item) for item in value.get('not_tested') or ()) or '-'}",
                "Launch 引用："
                + (
                    "；".join(
                        f"{item['source']}:{item['location']}" for item in references
                    )
                    or "无"
                ),
            )
        )
    )


def _status_label(status: str) -> str:
    return {
        "verified": "已验证",
        "pending": "未验证（尚未测试）",
        "retest_required": "需重新测试",
        "failed": "测试失败",
    }.get(status, status)


def _hash_label(value: object) -> str:
    return str(value)[:12] if isinstance(value, str) and value else "-"
