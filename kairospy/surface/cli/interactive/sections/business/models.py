"""Interactive Workspace AI model connections."""

from __future__ import annotations

import typer

from kairospy.application.agent import AgentResourceApplication
from kairospy.application.config import ConfigurationReferenceApplication

from ...models import (
    CommandExecution,
    GuidedCommand,
    InteractiveContext,
    ShellAction,
    ShellControl,
)


ROOT = ("resources", "models")


def print_menu(context: InteractiveContext) -> None:
    if context.shell_path == ROOT:
        connections = _connections(context)
        typer.echo("AI 模型：")
        if not connections:
            typer.echo("  还没有模型连接。可以连接云端服务、兼容接口或本机模型。")
        for index, value in enumerate(connections, start=1):
            models = value.get("models") or ()
            provider = (
                value.get("provider_label") or value.get("provider") or "模型服务"
            )
            typer.echo(
                f"  {index}. {value['connection_id']} · {provider} · "
                f"{_status_label(str(value.get('verification_status', 'pending')))} · "
                f"{len(models)} 个已保存模型"
            )
        typer.echo(f"  {len(connections) + 1}. 添加 AI 模型连接")
        return

    connection_id = context.shell_path[2]
    value = _connection(context, connection_id)
    models = value.get("models") or ()
    typer.echo(
        f"AI 模型：{connection_id}\n\n"
        f"状态：{_status_label(str(value.get('verification_status', 'pending')))}\n"
        f"类型：{value.get('provider_label') or value.get('provider') or '-'}\n"
        f"接口：{_mode_label(value.get('api_mode'))}\n"
        f"模型：{len(models)} 个已保存"
        + (f" · 最近测试 {value.get('model')}" if value.get("model") else "")
        + f"\n最近测试：{value.get('last_tested_at') or '尚未测试'}\n\n"
        "建议操作：\n"
        "  1. 测试连接\n"
        "  2. 查看已保存模型\n"
        "  3. 修改连接\n"
        "  4. 安全与高级信息\n"
        + ("  5. 启用\n" if value.get("enabled") is False else "  5. 禁用\n")
        + "  6. 删除"
    )


def print_help(context: InteractiveContext) -> None:
    if context.shell_path == ROOT:
        typer.echo("输入序号打开连接或添加新连接；b 返回运行准备。")
    else:
        typer.echo("输入 1-6 选择操作；b 返回 AI 模型列表。")


def handle(context: InteractiveContext, parts: tuple[str, ...]) -> ShellAction:
    if len(parts) != 1:
        return None
    key = parts[0]
    if context.shell_path == ROOT:
        connections = _connections(context)
        if key in {"n", "new", "setup", str(len(connections) + 1)}:
            return GuidedCommand(
                ("config", "agent", "setup"),
                "添加 AI 模型连接",
                execution=CommandExecution.INTERACTIVE,
                show_command=False,
            )
        if key.isdigit():
            index = int(key)
            if not 1 <= index <= len(connections):
                typer.echo(f"找不到模型连接序号：{key}")
                return ShellControl.HANDLED
            context.shell_path = (*ROOT, str(connections[index - 1]["connection_id"]))
            return ShellControl.HANDLED
        return None

    connection_id = context.shell_path[2]
    current = _connection(context, connection_id)
    if key in {"1", "test"}:
        saved_models = tuple(str(value) for value in current.get("models") or ())
        model = typer.prompt(
            "要测试的模型 ID",
            default=str(
                current.get("model") or (saved_models[0] if saved_models else "")
            ),
        ).strip()
        if not model:
            typer.echo("请先填写一个模型 ID。")
            return ShellControl.HANDLED
        return GuidedCommand(
            ("config", "agent", "test", connection_id, "--model", model),
            f"向 {connection_id} 发起最小文本调用；云端服务可能产生少量费用",
            dangerous=True,
        )
    if key in {"2", "models"}:
        models = tuple(str(value) for value in current.get("models") or ())
        typer.echo("已保存模型：" + ("、".join(models) if models else "暂无"))
        typer.echo("修改连接可重新填写；也可在配置向导完成后从服务读取模型。")
        return ShellControl.HANDLED
    if key in {"3", "edit", "setup"}:
        argv = [
            "config",
            "agent",
            "setup",
            "--provider",
            str(current.get("provider") or "custom"),
            "--connection-id",
            connection_id,
            "--api-mode",
            str(current.get("api_mode") or "openai-chat-completions"),
            "--base-url",
            str(current.get("base_url") or "http://127.0.0.1:8000/v1"),
        ]
        if current.get("credential_id"):
            argv.extend(("--credential-id", str(current["credential_id"])))
        if current.get("model"):
            argv.extend(("--model", str(current["model"])))
        return GuidedCommand(
            tuple(argv),
            f"修改 AI 模型连接 {connection_id}",
            execution=CommandExecution.INTERACTIVE,
            show_command=False,
        )
    if key in {"4", "advanced", "status"}:
        _print_advanced(context, current)
        return ShellControl.HANDLED
    if key in {"5", "disable", "enable"}:
        action = (
            "enable"
            if key == "enable" or (key == "5" and current.get("enabled") is False)
            else "disable"
        )
        return GuidedCommand(
            ("config", "agent", action, connection_id),
            f"{'启用' if action == 'enable' else '禁用'} AI 模型连接 {connection_id}",
            dangerous=action == "disable",
        )
    if key in {"6", "delete"}:
        return GuidedCommand(
            ("config", "agent", "delete", connection_id),
            f"删除未被运行方案引用的 AI 模型连接 {connection_id}",
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


def _print_advanced(context: InteractiveContext, value: dict[str, object]) -> None:
    credential_id = value.get("credential_id")
    references = (
        ConfigurationReferenceApplication(context.owner).model_connection_references(
            str(value.get("connection_id") or "")
        )
        if context.owner is not None
        else []
    )
    typer.echo(
        "安全与高级信息：\n"
        f"  Provider ID：{value.get('provider') or '-'}\n"
        f"  Base URL：{value.get('base_url') or '-'}\n"
        f"  认证资料：{credential_id or '无'}（值不显示）\n"
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


def _status_label(value: str) -> str:
    return {
        "verified": "可用",
        "failed": "连接失败",
        "retest_required": "配置已变化",
        "pending": "需要测试",
        "disabled": "已禁用",
    }.get(value, value)


def _mode_label(value: object) -> str:
    return {
        "openai-responses": "OpenAI Responses",
        "openai-chat-completions": "OpenAI Chat Completions",
        "anthropic-messages": "Anthropic Messages",
        "ollama-native": "Ollama Native",
    }.get(str(value), str(value or "-"))


def _hash_label(value: object) -> str:
    return str(value)[:12] if isinstance(value, str) and value else "-"
