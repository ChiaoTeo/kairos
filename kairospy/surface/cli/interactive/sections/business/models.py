"""Interactive Workspace OpenAI model connections."""

from __future__ import annotations

import typer

from kairospy.application.agent import AgentResourceApplication
from kairospy.application.config import ConfigurationReferenceApplication

from ...models import GuidedCommand, InteractiveContext, ShellAction, ShellControl


ROOT = ("resources", "models")


def print_menu(context: InteractiveContext) -> None:
    if context.shell_path == ROOT:
        typer.echo("模型连接：")
        connections = _connections(context)
        if not connections:
            typer.echo("  当前没有模型连接。")
        for index, value in enumerate(connections, start=1):
            typer.echo(
                f"  {index}. {value['connection_id']} · "
                f"{_status_label(str(value.get('verification_status', 'pending')))}"
                + (f" · {value.get('model')}" if value.get("model") else "")
            )
        typer.echo("  n. 添加 OpenAI 模型连接")
        return
    connection_id = context.shell_path[2]
    value = _connection(context, connection_id)
    references = (
        ConfigurationReferenceApplication(context.owner).credential_references(
            connection_id
        )
        if context.owner is not None
        else []
    )
    typer.echo(
        f"模型连接：{connection_id}\n"
        f"Provider：OpenAI · 模型：{value.get('model') or '尚未测试'}\n"
        f"状态：{_status_label(str(value.get('verification_status', 'pending')))}\n"
        f"上次测试：{value.get('last_tested_at') or '—'}\n"
        f"当前配置版本：{_hash_label(value.get('current_configuration_hash'))} · 测试版本：{_hash_label(value.get('tested_configuration_hash'))}\n"
        f"已测试：{', '.join(str(item) for item in value.get('tested') or ()) or '-'}\n"
        f"未测试：{', '.join(str(item) for item in value.get('not_tested') or ()) or '-'}\n"
        "Launch/资源引用："
        + (
            "；".join(f"{item['source']}:{item['location']}" for item in references)
            or "无"
        )
        + "\n"
        "  1. 手动执行最小模型调用测试\n"
        "  2. 查看连接状态\n"
        "  edit. 修改 SecretRef\n"
        "  delete. 删除连接（有引用时默认拒绝）"
    )


def print_help(context: InteractiveContext) -> None:
    if context.shell_path == ROOT:
        typer.echo("输入序号选择；n/setup 添加；back 返回运行资源。")
    else:
        typer.echo("可用命令：test/status/back/home。")


def handle(context: InteractiveContext, parts: tuple[str, ...]) -> ShellAction:
    if len(parts) != 1:
        return None
    if context.shell_path == ROOT:
        if parts[0] in {"n", "new", "setup"}:
            return GuidedCommand(
                ("config", "agent", "setup"), "配置 OpenAI 模型连接", dangerous=True
            )
        connections = _connections(context)
        if parts[0].isdigit():
            index = int(parts[0])
            if not 1 <= index <= len(connections):
                typer.echo(f"找不到模型连接序号：{parts[0]}")
                return ShellControl.HANDLED
            context.shell_path = (*ROOT, str(connections[index - 1]["connection_id"]))
            return ShellControl.HANDLED
        return None
    connection_id = context.shell_path[2]
    if parts[0] in {"1", "test"}:
        current = _connection(context, connection_id)
        model = typer.prompt(
            "固定模型 snapshot",
            default=str(current.get("model") or "gpt-5.4-2026-08-01"),
        ).strip()
        return GuidedCommand(
            ("config", "agent", "test", connection_id, "--model", model),
            f"测试 OpenAI 模型连接 {connection_id}；会产生少量调用费用",
            dangerous=True,
        )
    if parts[0] in {"2", "status"}:
        typer.echo(_connection(context, connection_id))
        return ShellControl.HANDLED
    if parts[0] in {"edit", "setup"}:
        return GuidedCommand(
            (
                "config",
                "credential",
                "setup",
                "--provider",
                "openai",
                "--credential-id",
                connection_id,
            ),
            f"修改 OpenAI 模型连接 {connection_id} 的 SecretRef",
            dangerous=True,
        )
    if parts[0] == "delete":
        return GuidedCommand(
            ("config", "credential", "delete", connection_id),
            f"删除未被 Launch 引用的模型连接 {connection_id}",
            dangerous=True,
        )
    return None


def _connections(context: InteractiveContext) -> tuple[dict[str, object], ...]:
    if context.owner is None:
        return ()
    return AgentResourceApplication(context.owner).model_connections()


def _connection(context: InteractiveContext, connection_id: str) -> dict[str, object]:
    for value in _connections(context):
        if value.get("connection_id") == connection_id:
            return value
    return {"connection_id": connection_id, "verification_status": "pending"}


def _status_label(value: str) -> str:
    return {
        "verified": "已验证",
        "failed": "测试失败",
        "retest_required": "需重新测试",
        "pending": "待测试",
    }.get(value, value)


def _hash_label(value: object) -> str:
    return str(value)[:12] if isinstance(value, str) and value else "-"
