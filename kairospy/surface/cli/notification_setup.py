from __future__ import annotations

from pathlib import Path
from typing import Literal

import typer

from kairospy.application.workspace.credentials import (
    CredentialConfigurationApplication,
    SecretRef,
)
from kairospy.application.notification import NotificationAdminApplication
from kairospy.application.notification.composition import test_notification_destination
from kairospy.application.workspace import Workspace
from kairospy.surface.cli.guided_setup import (
    CredentialMaterial,
    confirm_summary,
    configure_credential_material,
    print_step,
    prompt_credential_material,
)
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
    credentials = CredentialConfigurationApplication(workspace)
    default_id = "feishu-alerts" if selected == "feishu" else "telegram-alerts"
    print_step("通知提醒", 1, 6, "准备渠道")
    _print_primer(selected)
    print_step("通知提醒", 2, 6, "通知名称")
    destination_id = typer.prompt("名称", default=default_id).strip()
    credential_id = destination_id

    try:
        existing = admin.show(destination_id)
    except KeyError:
        existing = None
    if existing is not None:
        typer.echo(f"已存在通知渠道 {destination_id}，本次保存将更新它的配置。")

    try:
        existing_credential = credentials.show(credential_id)
    except KeyError:
        existing_credential = None
    print_step("通知提醒", 3, 6, "安全凭据")
    material = prompt_credential_material(
        credentials,
        credential_id,
        selected,
        existing=existing_credential,
    )
    secret = _material_secret(credentials, material, selected)
    chat_id: str | None = None
    if selected == "telegram":
        print_step("通知提醒", 4, 6, "验证并选择接收位置")
        if secret is None:
            typer.echo("当前无法读取 Bot Token，将跳过机器人验证和会话发现。")
            chat_id = typer.prompt("Telegram chat_id").strip()
        elif typer.confirm(
            "将调用 Telegram getMe/getUpdates；不会发送消息。开始验证吗？",
            default=True,
        ):
            chat_id = _telegram_chat(admin, secret)
        else:
            chat_id = typer.prompt("Telegram chat_id").strip()

    action = "更新" if existing is not None else "添加"
    provider_label = "Telegram" if selected == "telegram" else "飞书"
    print_step("通知提醒", 5, 6, "确认保存")
    rows = [
        ("名称", destination_id),
        ("渠道", provider_label),
        ("安全凭据", "已填写" if secret is not None else "外部引用"),
        ("当前操作", "只保存配置，不发送消息"),
    ]
    if chat_id is not None:
        rows.insert(2, ("接收位置", chat_id))
    if not confirm_summary(f"即将{action}通知提醒：", tuple(rows)):
        typer.echo("已取消，未修改通知配置。")
        return {
            "destination_id": destination_id,
            "provider": selected,
            "configured": existing is not None,
            "status": "cancelled",
        }

    credential = configure_credential_material(
        credentials,
        credential_id,
        selected,
        material,
        role="notification-send",
        overwrite=existing_credential is not None,
    )
    raw_reference = credential["secret_refs"][
        "bot_token" if selected == "telegram" else "webhook_url"
    ]
    secret_ref = SecretRef(str(raw_reference["source"]), str(raw_reference["id"]))  # type: ignore[arg-type]
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
    print_step("通知提醒", 6, 6, "真实消息测试")
    if result["secret_available"] and typer.confirm(
        f"现在向 {chat_id or provider_label} 发送一条真实测试消息吗？（推荐）",
        default=True,
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


def _telegram_chat(admin: NotificationAdminApplication, secret: str) -> str:
    identity = admin.probe_telegram_secret(secret)
    bot_label = f"@{identity.username}" if identity.username else identity.display_name
    typer.echo(f"已验证 Telegram Bot：{bot_label}（id={identity.bot_id}）")
    chats = admin.discover_telegram_chats_from_secret(secret)
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


def _material_secret(
    application: CredentialConfigurationApplication,
    material: CredentialMaterial,
    provider: str,
) -> str | None:
    field = "bot_token" if provider == "telegram" else "webhook_url"
    if material.values is not None:
        return material.values.get(field)
    if material.references is None:
        return None
    reference = material.references.get(field)
    return application.resolve(reference) if reference is not None else None


__all__ = ["run_notification_setup"]
