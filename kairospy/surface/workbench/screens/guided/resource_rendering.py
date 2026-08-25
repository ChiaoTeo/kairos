"""Rich renderers and display identities for runtime resources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rich.console import RenderableType
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table


RESOURCE_LABELS = {
    "accounts": "交易账户",
    "data": "市场数据",
    "models": "AI 模型",
    "notifications": "通知提醒",
}


def identity(kind: str, record: Mapping[str, Any]) -> str:
    keys = {
        "accounts": ("account_id", "id", "name"),
        "data": ("connection_id", "id", "name"),
        "models": ("connection_id", "id", "name"),
        "notifications": ("destination_id", "id", "name"),
    }.get(kind, ("id", "name"))
    for key in keys:
        value = record.get(key)
        if value:
            return str(value)
    return "unknown"


def record_summary(kind: str, record: Mapping[str, Any]) -> str:
    """Return the concise, user-facing facts shown in a resource list."""

    facts: list[str] = []
    provider = record.get("provider") or record.get("broker") or record.get("sender")
    if provider:
        facts.append(str(provider))

    if kind == "accounts":
        environment = record.get("environment")
        if environment:
            facts.append(str(environment))
        segments = record.get("segments") or ()
        if segments:
            facts.append("/".join(map(str, segments)))
        role = record.get("credential_role")
        if role:
            facts.append(str(role))
    elif kind == "models":
        models = record.get("models") or ()
        if models:
            facts.append(f"{len(models)} 个模型")
    elif kind == "notifications":
        channel = record.get("channel") or record.get("route")
        if channel:
            facts.append(str(channel))

    if (
        not record.get("enabled", True)
        or str(record.get("status") or "").lower() == "disabled"
    ):
        facts.append("已停用")
    else:
        verification = {
            "verified": "已验证",
            "pending": "待验证",
            "failed": "验证失败",
            "retest_required": "需重新验证",
            "disabled": "已停用",
        }.get(str(record.get("verification_status") or "pending"))
        if verification:
            facts.append(verification)

    issues = record.get("issues") or ()
    if issues:
        facts.append("；".join(map(str, issues)))
    return " · ".join(facts) or "查看详情"


def records_renderable(
    kind: str, records: tuple[dict[str, Any], ...]
) -> RenderableType:
    label = RESOURCE_LABELS.get(kind, kind)
    if not records:
        return Panel("尚未配置。输入 /new 开始添加。", title=label)
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right", style="bold cyan")
    table.add_column("名称")
    table.add_column("Provider")
    table.add_column("启用")
    table.add_column("验证")
    table.add_column("问题")
    for index, record in enumerate(records, 1):
        issues = record.get("issues") or ()
        table.add_row(
            str(index),
            identity(kind, record),
            str(
                record.get("provider")
                or record.get("broker")
                or record.get("sender")
                or "—"
            ),
            "是" if record.get("enabled", True) else "否",
            str(record.get("verification_status") or "pending"),
            "；".join(map(str, issues)) or "—",
        )
    return Panel(table, title=f"{len(records)} 个{label}", border_style="cyan")


def summary_renderable(values: Mapping[str, tuple[int, int]]) -> RenderableType:
    table = Table(show_header=True, header_style="bold")
    table.add_column("资源")
    table.add_column("已配置", justify="right")
    table.add_column("已验证", justify="right")
    table.add_column("状态")
    for kind, label in RESOURCE_LABELS.items():
        count, verified = values.get(kind, (0, 0))
        status = (
            "尚未配置" if not count else "可用" if count == verified else "需要处理"
        )
        table.add_row(label, str(count), str(verified), status)
    return Panel(table, title="运行资源检查", border_style="cyan")


def detail_renderable(kind: str, record: Mapping[str, Any]) -> RenderableType:
    return Panel(
        Pretty(dict(record), expand_all=True),
        title=f"{RESOURCE_LABELS.get(kind, kind)} · {identity(kind, record)}",
        border_style="cyan",
    )


__all__ = [
    "RESOURCE_LABELS",
    "detail_renderable",
    "identity",
    "record_summary",
    "records_renderable",
    "summary_renderable",
]
