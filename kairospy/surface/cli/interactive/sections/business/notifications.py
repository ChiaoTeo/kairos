"""Interactive notification destination section."""

from __future__ import annotations

import typer

from kairospy.application.config import ConfigurationReferenceApplication
from kairospy.application.notification import NotificationAdminApplication

from ...models import GuidedCommand, InteractiveContext, ShellAction, ShellControl


RESOURCE_ROOT = ("resources", "notifications")


def print_menu(context: InteractiveContext) -> None:
    if context.shell_path[:2] == RESOURCE_ROOT:
        destinations = _destinations(context)
        if len(context.shell_path) == 2:
            typer.echo("通知渠道：")
            if not destinations:
                typer.echo("  当前没有 Destination。")
            for index, value in enumerate(destinations, start=1):
                typer.echo(
                    f"  {index}. {value['destination_id']} · {value.get('provider')} · "
                    f"{_status_label(str(value.get('verification_status') or 'pending'))}"
                )
            typer.echo("  n. 添加飞书或 Telegram 渠道\n  t. 发送真实测试消息")
            return
        _print_resource_detail(context, context.shell_path[2])
        return
    typer.echo(
        "通知：\n"
        "  1. 引导配置飞书\n"
        "  2. 引导配置 Telegram\n"
        "  3. 查看 Destination 状态\n"
        "  4. 绑定 Destination 到 Launch\n"
        "  5. 校验通知配置\n"
        "  6. 测试通知目的地\n"
        "  7. 禁用通知目的地\n"
        "  8. 删除通知目的地"
    )


def print_help(context: InteractiveContext) -> None:
    if context.shell_path[:2] == RESOURCE_ROOT:
        typer.echo("可用命令：<序号>/new/test/list/disable/delete/references/back/home")
        return
    typer.echo("可用命令：feishu/telegram/list/attach/validate/test/disable/delete")


def handle(context: InteractiveContext, parts: tuple[str, ...]) -> ShellAction:
    if len(parts) != 1:
        return None
    if context.shell_path[:2] == RESOURCE_ROOT:
        return _handle_resource(context, parts[0])
    if parts[0] in {"1", "feishu"}:
        return GuidedCommand(
            ("notifications", "setup", "--provider", "feishu"),
            "引导配置飞书通知",
            dangerous=True,
        )
    if parts[0] in {"2", "telegram"}:
        return GuidedCommand(
            ("notifications", "setup", "--provider", "telegram"),
            "引导配置 Telegram 通知",
            dangerous=True,
        )
    if parts[0] in {"3", "list", "status"}:
        return GuidedCommand(
            ("notifications", "list", "--format", "table"),
            "查看通知 Destination 状态",
        )
    if parts[0] in {"4", "attach"}:
        destination_id = typer.prompt("Destination ID").strip()
        launch_id = typer.prompt("Launch ID").strip()
        route = typer.prompt("Route", default="signals").strip()
        return GuidedCommand(
            (
                "notifications",
                "attach",
                destination_id,
                "--launch",
                launch_id,
                "--route",
                route,
                "--default-route",
            ),
            "绑定通知 Destination 到 Launch",
            dangerous=True,
        )
    if parts[0] in {"5", "validate"}:
        mode = typer.prompt("mode", default="paper").strip()
        return GuidedCommand(
            ("notifications", "validate", "--mode", mode, "--format", "text"),
            "校验通知目的地",
        )
    if parts[0] in {"6", "test"}:
        destination_id = typer.prompt("destination id").strip()
        return GuidedCommand(
            ("notifications", "test", destination_id, "--format", "text"),
            "测试通知目的地",
            dangerous=True,
        )
    if parts[0] in {"7", "disable"}:
        destination_id = typer.prompt("Destination ID").strip()
        return GuidedCommand(
            ("notifications", "disable", destination_id, "--format", "text"),
            "禁用通知 Destination",
            dangerous=True,
        )
    if parts[0] in {"8", "delete"}:
        destination_id = typer.prompt("Destination ID").strip()
        return GuidedCommand(
            ("notifications", "delete", destination_id, "--format", "text"),
            "删除通知 Destination",
            dangerous=True,
        )
    return None


def _handle_resource(context: InteractiveContext, key: str) -> ShellAction:
    destinations = _destinations(context)
    if len(context.shell_path) == 2:
        if key in {"n", "new", "setup"}:
            provider = (
                typer.prompt("Provider [telegram/feishu]", default="telegram")
                .strip()
                .lower()
            )
            if provider not in {"telegram", "feishu"}:
                raise typer.BadParameter("Provider 只能是 telegram 或 feishu")
            return GuidedCommand(
                ("notifications", "setup", "--provider", provider),
                f"配置 {provider} Destination SecretRef",
                dangerous=True,
            )
        if key in {"t", "test"}:
            destination_id = typer.prompt("Destination id").strip()
            return _test_command(destination_id)
        if key in {"list", "ls"}:
            print_menu(context)
            return ShellControl.HANDLED
        if key.isdigit():
            index = int(key)
            if not 1 <= index <= len(destinations):
                typer.echo(f"找不到通知渠道序号：{key}")
                return ShellControl.HANDLED
            context.shell_path = (
                *RESOURCE_ROOT,
                str(destinations[index - 1]["destination_id"]),
            )
            return ShellControl.HANDLED
        return None
    destination_id = context.shell_path[2]
    if key in {"t", "test"}:
        return _test_command(destination_id)
    if key in {"disable"}:
        return GuidedCommand(
            ("notifications", "disable", destination_id, "--format", "text"),
            f"禁用通知渠道 {destination_id}",
            dangerous=True,
        )
    if key in {"delete"}:
        return GuidedCommand(
            ("notifications", "delete", destination_id, "--format", "text"),
            f"删除未被 Launch 引用的通知渠道 {destination_id}",
            dangerous=True,
        )
    if key in {"references", "uses"}:
        _print_references(context, destination_id)
        return ShellControl.HANDLED
    return None


def _test_command(destination_id: str) -> GuidedCommand:
    return GuidedCommand(
        ("notifications", "test", destination_id, "--format", "text"),
        f"向 {destination_id} 发送真实测试消息；可能产生外部通知",
        dangerous=True,
    )


def _destinations(context: InteractiveContext) -> list[dict[str, object]]:
    if context.owner is None:
        return []
    try:
        return NotificationAdminApplication(context.owner).list()
    except (OSError, ValueError):
        return []


def _print_resource_detail(context: InteractiveContext, destination_id: str) -> None:
    value = next(
        (
            destination
            for destination in _destinations(context)
            if destination.get("destination_id") == destination_id
        ),
        {"destination_id": destination_id},
    )
    references = _references(context, destination_id)
    typer.echo(
        "\n".join(
            (
                f"通知渠道：{destination_id}",
                f"Provider：{value.get('provider') or '-'} · {'启用' if value.get('enabled', False) else '禁用'}",
                f"状态：{_status_label(str(value.get('verification_status') or 'pending'))}",
                f"安全凭据：{value.get('credential_id') or '-'}（值不显示）",
                f"最近测试：{value.get('last_tested_at') or '-'}",
                f"当前配置版本：{_hash_label(value.get('current_configuration_hash'))} · 测试版本：{_hash_label(value.get('tested_configuration_hash'))}",
                f"测试结果：{value.get('last_test_detail') or '-'}",
                f"已测试：{', '.join(str(item) for item in value.get('tested') or ()) or '-'}",
                f"未测试：{', '.join(str(item) for item in value.get('not_tested') or ()) or '-'}",
                "Launch 引用："
                + (
                    "；".join(
                        f"{item['source']}:{item['location']}" for item in references
                    )
                    or "无"
                ),
                "  t. 发送真实测试消息",
                "  disable. 禁用",
                "  delete. 删除（有引用时默认拒绝）",
            )
        )
    )


def _print_references(context: InteractiveContext, destination_id: str) -> None:
    references = _references(context, destination_id)
    if not references:
        typer.echo("当前没有 Launch 引用。")
        return
    for item in references:
        typer.echo(f"{item['source']}:{item['location']}")


def _references(
    context: InteractiveContext, destination_id: str
) -> list[dict[str, str]]:
    if context.owner is None:
        return []
    return ConfigurationReferenceApplication(context.owner).destination_references(
        destination_id
    )


def _status_label(status: str) -> str:
    return {
        "verified": "已验证",
        "pending": "待测试",
        "retest_required": "需重新测试",
        "failed": "测试失败",
    }.get(status, status)


def _hash_label(value: object) -> str:
    return str(value)[:12] if isinstance(value, str) and value else "-"


def choose() -> GuidedCommand:
    command = handle(
        InteractiveContext(None, None, None, shell_path=("notifications",)), ("feishu",)
    )
    assert command is not None
    return command
