from __future__ import annotations

from pathlib import Path
from typing import Literal

import typer

from kairospy.application.notification import (
    NotificationAdminApplication,
    NotificationSecretRef,
)
from kairospy.application.notification.composition import test_notification_destination
from kairospy.application.workspace import Workspace
from kairospy.surface.cli.options import OutputFormat, render


def run_notification_setup(
    workspace: Workspace,
    *,
    provider: str | None,
    output: OutputFormat,
) -> dict[str, object]:
    """Run the provider-aware setup wizard and return a secret-free summary."""

    selected = _provider(provider)
    admin = NotificationAdminApplication(workspace)
    default_id = "feishu-alerts" if selected == "feishu" else "telegram-alerts"
    _print_primer(selected)
    destination_id = typer.prompt("Destination ID", default=default_id).strip()
    credential_id = typer.prompt("Credential ID", default=destination_id).strip()

    try:
        existing = admin.show(destination_id)
    except KeyError:
        existing = None
    if existing is not None:
        typer.echo(f"已存在通知渠道 {destination_id}，本次保存将更新它的配置。")

    secret_ref = _secret_reference(admin, credential_id, selected)
    chat_id: str | None = None
    if selected == "telegram":
        chat_id = _telegram_chat(admin, secret_ref)

    action = "更新" if existing is not None else "添加"
    provider_label = "Telegram" if selected == "telegram" else "飞书"
    typer.echo()
    typer.echo(f"即将{action}通知渠道：")
    typer.echo(f"  渠道名称   {destination_id}")
    typer.echo(f"  渠道类型   {provider_label}")
    if chat_id is not None:
        typer.echo(f"  接收目标   {chat_id}")
    typer.echo(f"  凭据引用   {secret_ref.source}:{secret_ref.id}（不保存凭据值）")
    typer.echo("  运行影响   不会启动、停止或修改任何策略")
    if not typer.confirm("确认保存这个通知渠道吗？", default=True):
        typer.echo("已取消，未修改通知配置。")
        return {
            "destination_id": destination_id,
            "provider": selected,
            "configured": existing is not None,
            "status": "cancelled",
        }

    result = admin.configure(
        destination_id,
        provider=selected,
        credential_id=credential_id,
        secret_ref=secret_ref,
        chat_id=chat_id,
    )
    typer.echo("通知渠道已保存。")
    typer.echo(render(result, output))
    if not result["secret_available"]:
        typer.echo(
            f"SecretRef 尚不可解析：{secret_ref.source}:{secret_ref.id}。"
            "在运行 Kairos 的环境中提供它后再执行测试。"
        )

    result = {
        **result,
        "next_action": "select this Destination while editing a Launch",
    }
    typer.echo("下一步：可将此通知渠道绑定到策略配置，并选择要接收的通知类型。")
    if result["secret_available"] and typer.confirm(
        "现在发送一条真实测试消息以完成验证吗？（推荐）", default=True
    ):
        import asyncio

        result = {
            **result,
            "test": asyncio.run(
                test_notification_destination(workspace, destination_id)
            ),
        }
        typer.echo(render(result["test"], output))
        typer.echo("测试完成；可返回工作台查看最新验证状态。")
    return result


def _provider(value: str | None) -> Literal["feishu", "telegram"]:
    selected = (value or "").strip().lower()
    if not selected:
        choice = typer.prompt("通知渠道（1=飞书，2=Telegram）", default="1").strip()
        selected = {"1": "feishu", "2": "telegram"}.get(choice, choice.lower())
    if selected not in {"feishu", "telegram"}:
        raise typer.BadParameter("provider must be feishu or telegram")
    return selected  # type: ignore[return-value]


def _print_primer(provider: str) -> None:
    if provider == "feishu":
        typer.echo("飞书配置准备：")
        typer.echo("1. 在目标群中添加“自定义机器人”。")
        typer.echo("2. 复制机器人 Webhook URL。")
        typer.echo("3. 当前适配器不支持签名校验，请使用关键词或 IP 白名单。")
        return
    typer.echo("Telegram 配置准备：")
    typer.echo("1. 通过 @BotFather 创建机器人并取得 Bot Token。")
    typer.echo("2. 向机器人或目标群发送一条消息。")
    typer.echo("3. Kairos 会尝试通过 getMe/getUpdates 验证机器人并发现 chat_id。")


def _secret_reference(
    admin: NotificationAdminApplication, credential_id: str, provider: str
) -> NotificationSecretRef:
    source = typer.prompt(
        "Secret 来源（1=环境变量，推荐；2=文件）", default="1"
    ).strip()
    source = {"1": "env", "2": "file"}.get(source, source.lower())
    if source == "env":
        default = admin.default_secret_environment(credential_id, provider)
        identifier = typer.prompt("环境变量名", default=default).strip()
        return NotificationSecretRef("env", identifier)
    if source == "file":
        identifier = typer.prompt(
            "Secret 文件路径（绝对路径或相对 .kairos 的路径）"
        ).strip()
        return NotificationSecretRef("file", identifier)
    raise typer.BadParameter("Secret source must be env or file")


def _telegram_chat(
    admin: NotificationAdminApplication, reference: NotificationSecretRef
) -> str:
    secret = admin.resolve_secret(reference)
    if secret is None:
        typer.echo("当前无法解析 Bot Token，将跳过在线验证和 chat 自动发现。")
        return typer.prompt("Telegram chat_id").strip()
    identity = admin.probe_telegram_reference(reference)
    bot_label = f"@{identity.username}" if identity.username else identity.display_name
    typer.echo(f"已验证 Telegram Bot：{bot_label}（id={identity.bot_id}）")
    chats = admin.discover_telegram_chats_from_reference(reference)
    if not chats:
        typer.echo(
            "没有发现 chat。请先向机器人或目标群发送消息，然后重试；也可手动输入。"
        )
        return typer.prompt("Telegram chat_id").strip()
    typer.echo("发现以下 Telegram chat：")
    for index, chat in enumerate(chats, start=1):
        typer.echo(f"  {index}. {chat.title} · {chat.kind} · {chat.chat_id}")
    choice = typer.prompt("选择序号，或直接输入 chat_id", default="1").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(chats):
        return chats[int(choice) - 1].chat_id
    return choice


__all__ = ["run_notification_setup"]
