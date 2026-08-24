"""Workspace running-resource product entry."""

from __future__ import annotations

import typer

from kairospy.application.account import AccountConfigurationApplication
from kairospy.application.agent import AgentResourceApplication
from kairospy.application.notification import NotificationAdminApplication
from kairospy.application.reference import ReferenceProviderConfigurationApplication

from ...models import InteractiveContext, ShellAction, ShellControl


def print_menu(context: InteractiveContext) -> None:
    summary = resource_summary(context)
    needs_action = int(summary["needs_action"])
    typer.echo("运行准备")
    if needs_action:
        typer.echo(f"⚠ 还有 {needs_action} 个已配置连接需要处理")
    else:
        typer.echo("已配置连接没有待处理问题")
    typer.echo(
        f"  1. 交易账户       {_label(summary['accounts'])}\n"
        f"  2. 市场数据       {_label(summary['data'])}\n"
        f"  3. AI 模型         {_label(summary['models'])}\n"
        f"  4. 通知提醒       {_label(summary['notifications'], optional=True)}\n"
        "  5. 检查所有连接"
    )


def print_help(context: InteractiveContext) -> None:
    del context
    typer.echo(
        "输入序号选择；可用命令：accounts/data/models/notifications/check/back/home。"
    )


def handle(context: InteractiveContext, parts: tuple[str, ...]) -> ShellAction:
    if len(parts) != 1:
        return None
    route = {
        "1": "accounts",
        "accounts": "accounts",
        "2": "data",
        "data": "data",
        "3": "models",
        "models": "models",
        "4": "notifications",
        "notifications": "notifications",
    }.get(parts[0])
    if route is not None:
        context.shell_path = ("resources", route)
        return ShellControl.HANDLED
    if parts[0] in {"5", "check", "doctor"}:
        print_readiness(context)
        return ShellControl.HANDLED
    return None


def resource_summary(context: InteractiveContext) -> dict[str, object]:
    if context.owner is None:
        empty = {"count": 0, "verified": 0, "needs_action": 0}
        return {
            "accounts": empty,
            "data": empty,
            "models": empty,
            "notifications": empty,
            "needs_action": 0,
        }
    accounts = AccountConfigurationApplication(context.owner).list()
    account_verified = sum(
        value.get("verification_status") == "verified" for value in accounts
    )
    data = ReferenceProviderConfigurationApplication(context.owner).list()
    data_verified = sum(
        value.get("verification_status") == "verified" for value in data
    )
    models = list(AgentResourceApplication(context.owner).model_connections())
    model_verified = sum(
        value.get("verification_status") == "verified" for value in models
    )
    notifications = NotificationAdminApplication(context.owner).list()
    notification_verified = sum(
        value.get("verification_status") == "verified" for value in notifications
    )
    groups = {
        "accounts": _counts(len(accounts), account_verified),
        "data": _counts(len(data), data_verified),
        "models": _counts(len(models), model_verified),
        "notifications": _counts(len(notifications), notification_verified),
    }
    return {
        **groups,
        "needs_action": sum(int(value["needs_action"]) for value in groups.values()),
    }


def print_readiness(context: InteractiveContext) -> None:
    summary = resource_summary(context)
    typer.echo("连接检查：")
    for key, label in (
        ("accounts", "交易账户"),
        ("data", "市场数据"),
        ("models", "AI 模型"),
        ("notifications", "通知提醒（可选）"),
    ):
        typer.echo(f"  {label}：{_label(summary[key])}")
    if summary["needs_action"]:
        typer.echo("下一步：打开标为“需要测试”或“连接失败”的连接并完成测试。")
    else:
        typer.echo("所有已配置连接均可用；未配置的可选连接不会阻止运行。")


def _counts(count: int, verified: int) -> dict[str, int]:
    return {"count": count, "verified": verified, "needs_action": count - verified}


def _label(value: object, *, optional: bool = False) -> str:
    counts = value if isinstance(value, dict) else {}
    count = int(counts.get("count", 0))
    verified = int(counts.get("verified", 0))
    pending = int(counts.get("needs_action", 0))
    if not count:
        return "尚未配置（可选）" if optional else "尚未配置"
    if not pending:
        return f"{verified}/{count} 可用"
    return f"{verified}/{count} 可用 · {pending} 个需要处理"
