"""Rich views and display identities for runtime resources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table
from rich.text import Text


RESOURCE_LABELS = {
    "accounts": "交易账户",
    "data": "市场数据",
    "model_endpoints": "模型服务",
    "models": "可用模型",
    "notifications": "通知提醒",
}


_FIELD_LABELS = {
    "account_id": "账户",
    "connection_id": "连接",
    "endpoint_id": "模型服务",
    "model_id": "可用模型",
    "provider_model": "服务商模型 ID",
    "destination_id": "提醒",
    "provider": "Provider",
    "broker": "Broker",
    "environment": "环境",
    "enabled": "启用状态",
    "configured": "配置状态",
    "credential_id": "凭据",
    "secret_available": "凭据状态",
    "chat_id": "接收目标",
    "endpoint": "Endpoint",
    "base_url": "Base URL",
    "api_mode": "API 模式",
    "models": "模型",
    "model": "模型",
    "message": "你的消息",
    "response": "模型回复",
    "verified_models": "已验证模型",
    "failed_models": "验证失败模型",
    "stale_models": "需要重测模型",
    "segments": "市场分段",
    "credential_role": "凭据权限",
    "verification_status": "验证状态",
    "last_tested_at": "最近测试",
    "status": "状态",
    "publish_status": "提交状态",
    "succeeded": "测试结果",
    "tested": "已尝试检查",
    "not_tested": "未执行检查",
    "capabilities_verified": "已验证能力",
    "capabilities": "能力",
    "products": "产品",
    "purposes": "系统用途",
    "observed_permissions": "Provider 实际权限",
    "warnings": "提示",
    "facts": "验证事实",
    "issues": "问题",
    "error_category": "错误类别",
    "resource": "资源",
    "action": "操作",
    "value": "参数",
    "launch_id": "Launch",
}

_TECHNICAL_FIELDS = {
    "current_configuration_hash",
    "tested_configuration_hash",
    "configuration_hash",
    "config_hash",
    "schema_version",
    "config_schema_version",
    "health",
    "references",
}

_STATUS_LABELS = {
    "accepted": "已接受",
    "configured": "已配置",
    "deleted": "已删除",
    "disabled": "已停用",
    "failed": "失败",
    "healthy": "健康",
    "pending": "待验证",
    "preview": "预览",
    "rejected": "已拒绝",
    "retest_required": "需要重新验证",
    "verified": "已验证",
}


def identity(kind: str, record: Mapping[str, Any]) -> str:
    keys = {
        "accounts": ("account_id", "id", "name"),
        "data": ("connection_id", "id", "name"),
        "model_endpoints": ("endpoint_id", "id", "name"),
        "models": ("model_id", "id", "name"),
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
    elif kind == "data":
        products = record.get("products") or ()
        if products:
            facts.append("/".join(map(str, products)))
    elif kind == "models":
        facts.extend(
            str(value)
            for value in (record.get("provider_model"), record.get("endpoint_id"))
            if value
        )
    elif kind == "model_endpoints":
        if record.get("api_mode"):
            facts.append(str(record["api_mode"]))
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
    if kind == "models":
        table.add_column("模型")
    table.add_column("启用")
    table.add_column("验证")
    table.add_column("问题")
    for index, record in enumerate(records, 1):
        issues = record.get("issues") or ()
        row = [
            str(index),
            identity(kind, record),
            str(
                record.get("provider")
                or record.get("broker")
                or record.get("sender")
                or "—"
            ),
        ]
        if kind == "models":
            row.append(str(record.get("provider_model") or "—"))
        row.extend(
            (
                "是" if record.get("enabled", True) else "否",
                str(record.get("verification_status") or "pending"),
                "；".join(map(str, issues)) or "—",
            )
        )
        table.add_row(*row)
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
    return mapping_renderable(
        record,
        title=f"{RESOURCE_LABELS.get(kind, kind)} · {identity(kind, record)}",
        preferred_fields={
            "accounts": (
                "account_id",
                "broker",
                "environment",
                "segments",
                "credential_role",
                "enabled",
                "verification_status",
                "last_tested_at",
                "issues",
            ),
            "data": (
                "connection_id",
                "provider",
                "endpoint",
                "environment",
                "products",
                "purposes",
                "credential_id",
                "secret_available",
                "enabled",
                "verification_status",
                "last_tested_at",
                "capabilities_verified",
                "observed_permissions",
                "warnings",
                "issues",
            ),
            "models": (
                "model_id",
                "endpoint_id",
                "provider_model",
                "enabled",
                "configured",
                "verification_status",
                "last_tested_at",
                "issues",
            ),
            "notifications": (
                "destination_id",
                "provider",
                "enabled",
                "credential_id",
                "secret_available",
                "chat_id",
                "configured",
                "verification_status",
                "last_tested_at",
                "issues",
            ),
        }.get(kind),
    )


def saved_resource_renderable(
    kind: str, record: Mapping[str, Any], *, title: str
) -> RenderableType:
    """Render a save result as a compact handoff to the next useful action."""

    if kind != "models":
        return mapping_renderable(record, title=title)

    provider_model = str(record.get("provider_model") or "—")
    endpoint_id = str(record.get("endpoint_id") or "—")
    enabled = bool(record.get("enabled", True))
    verification = str(record.get("verification_status") or "pending").lower()
    status_label = {
        "verified": "已验证",
        "pending": "待验证",
        "failed": "验证失败",
        "retest_required": "需要重新验证",
        "disabled": "已停用",
    }.get(verification, verification)
    status_style = {
        "verified": "bold green",
        "failed": "bold red",
        "pending": "bold yellow",
        "retest_required": "bold yellow",
        "disabled": "dim",
    }.get(verification, "")

    facts = Text()
    facts.append(provider_model)
    facts.append(f" · 模型服务 {endpoint_id} · ", style="dim")
    facts.append("已启用" if enabled else "已停用")
    facts.append(" · ", style="dim")
    facts.append(status_label, style=status_style)

    if verification == "verified":
        next_step = Text("该模型已经可以用于 Launch。", style="dim")
    elif not enabled or verification == "disabled":
        next_step = Text("下一步：启用该模型后再进行验证。", style="dim")
    else:
        next_step = Text("下一步：选择该模型，开始对话验证。", style="yellow")
    return Group(facts, next_step)


def action_result_renderable(
    kind: str | None,
    action: str,
    result: Any,
    *,
    title: str,
) -> RenderableType:
    """Render the outcome a person needs; reserve raw records for Advanced."""

    if action == "advanced" or not isinstance(result, Mapping):
        return Panel(Pretty(result, expand_all=True), title=title)
    if kind == "data" and action == "test":
        return _data_test_renderable(result, title=title)
    if kind == "notifications" and action == "test":
        return _notification_test_renderable(result, title=title)
    if kind == "models" and action == "test" and "message" in result:
        return mapping_renderable(
            result,
            title=title,
            preferred_fields=(
                "succeeded",
                "model",
                "message",
                "response",
                "error_category",
            ),
        )
    return mapping_renderable(result, title=title)


def model_catalog_renderable(record: Mapping[str, Any]) -> RenderableType:
    """Show known models and ModelRef-scoped verification state."""

    verified = set(map(str, record.get("verified_models") or ()))
    failed = set(map(str, record.get("failed_models") or ()))
    stale = set(map(str, record.get("stale_models") or ()))
    models = tuple(
        dict.fromkeys(
            map(
                str,
                (
                    *(record.get("models") or ()),
                    *verified,
                    *failed,
                    *stale,
                ),
            )
        )
    )
    if not models:
        return Panel(
            "尚未发现模型。请选择“重新发现模型”或测试一个模型 ID。", title="模型状态"
        )
    table = Table(show_header=True, header_style="bold")
    table.add_column("模型 ID")
    table.add_column("验证状态")
    for model in models:
        status = (
            "已验证"
            if model in verified
            else "验证失败"
            if model in failed
            else "需要重测"
            if model in stale
            else "待验证"
        )
        table.add_row(model, status)
    return Panel(table, title="模型状态", border_style="cyan")


def mapping_renderable(
    record: Mapping[str, Any],
    *,
    title: str,
    preferred_fields: tuple[str, ...] | None = None,
) -> RenderableType:
    """Project a record into a compact user-facing field table."""

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    fields = preferred_fields or tuple(record)
    shown = 0
    for key in fields:
        if key in _TECHNICAL_FIELDS or key not in record:
            continue
        value = record[key]
        if value is None or value == [] or value == {}:
            continue
        table.add_row(
            _FIELD_LABELS.get(key, key.replace("_", " ")), _display(value, key)
        )
        shown += 1
    if not shown:
        table.add_row("结果", "已完成")
    return Panel(table, title=title, border_style="cyan")


def _notification_test_renderable(
    result: Mapping[str, Any], *, title: str
) -> RenderableType:
    health = result.get("health")
    health = health if isinstance(health, Mapping) else {}
    destination_id = str(result.get("destination_id") or "")
    destinations = health.get("destinations")
    destinations = destinations if isinstance(destinations, Mapping) else {}
    destination = destinations.get(destination_id)
    destination = destination if isinstance(destination, Mapping) else {}
    delivered = int(
        destination.get("delivered_total") or health.get("delivered_total") or 0
    )
    failed = int(destination.get("failed_total") or health.get("failed_total") or 0)

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    if delivered and not failed:
        table.add_row("结果", Text("测试消息已送达", style="bold green"))
    elif failed:
        table.add_row("结果", Text("测试消息发送失败", style="bold red"))
    else:
        table.add_row(
            "结果", _display(result.get("publish_status") or "已提交", "publish_status")
        )
    table.add_row("提醒", destination_id or "—")
    sender = destination.get("sender")
    if sender:
        table.add_row("Provider", str(sender))
    delivered_at = destination.get("last_success_at") or health.get("last_delivery_at")
    if delivered_at:
        table.add_row("送达时间", str(delivered_at))
    notification_id = str(result.get("notification_id") or "")
    if notification_id:
        table.add_row("消息编号", notification_id[:12])
    error_code = destination.get("last_error_code")
    if error_code:
        table.add_row("错误", Text(str(error_code), style="red"))
    return Panel(
        table, title=title, border_style="green" if delivered and not failed else "cyan"
    )


def _data_test_renderable(result: Mapping[str, Any], *, title: str) -> RenderableType:
    """Explain a market-data verification without exposing provider payloads."""

    status = str(result.get("verification_status") or "").lower()
    failed = status == "failed" or result.get("succeeded") is False
    verified = status == "verified" or result.get("succeeded") is True

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    if failed:
        table.add_row("结果", Text("连接验证失败", style="bold red"))
        category = str(result.get("error_category") or "provider_response")
        reason, suggestion = _verification_failure_guidance(category)
        table.add_row("失败原因", reason)
    elif verified:
        table.add_row("结果", Text("连接验证通过", style="bold green"))
        suggestion = ""
    else:
        table.add_row(
            "结果",
            _display(
                result.get("verification_status") or "pending", "verification_status"
            ),
        )
        suggestion = ""

    tested = result.get("tested") or ()
    if tested:
        table.add_row("已尝试检查", _display(tested))
    capabilities = result.get("capabilities_verified") or ()
    if capabilities:
        table.add_row("已验证能力", _display(capabilities))
    not_tested = result.get("not_tested") or ()
    if not_tested:
        table.add_row("未执行检查", _display(not_tested))
    tested_at = result.get("last_tested_at")
    if tested_at:
        table.add_row("测试时间", str(tested_at))
    if suggestion:
        table.add_row("建议", Text(suggestion, style="yellow"))
    return Panel(
        table,
        title=title,
        border_style="red" if failed else "green" if verified else "cyan",
    )


def _verification_failure_guidance(category: str) -> tuple[str, str]:
    return {
        "credential_missing": (
            "凭据缺失或 API Key 不可用",
            "修改配置并选择包含 api_key 的 Massive 凭据，然后重新测试。",
        ),
        "authentication_or_entitlement": (
            "API Key 无效，或当前套餐没有所测数据权限",
            "检查凭据和 Massive 套餐权限；确认后重新测试。",
        ),
        "rate_limited": (
            "Provider 暂时限制了请求频率",
            "稍后重新测试；若持续发生，请检查 Massive 的频率配额。",
        ),
        "provider_http": (
            "Provider 返回了非预期的 HTTP 错误",
            "检查 Endpoint 和 Provider 服务状态，然后重新测试。",
        ),
        "network": (
            "无法连接 Provider，或请求超时",
            "检查网络、DNS、代理和 Endpoint，然后重新测试。",
        ),
        "invalid_response": (
            "Provider 返回了无法识别的响应",
            "检查 Endpoint 是否为 Massive API 兼容地址；确认响应格式后重新测试。",
        ),
        "provider_response": (
            "Provider 响应未通过连接验证",
            "检查 Endpoint、凭据和数据权限，然后重新测试。",
        ),
    }.get(
        category,
        (
            f"连接验证失败（{category}）",
            "检查连接配置和 Provider 状态，然后重新测试。",
        ),
    )


def _display(value: Any, key: str = "") -> str:
    if key == "chat_id":
        text = str(value)
        return f"****{text[-4:]}" if len(text) > 4 else "****"
    if isinstance(value, bool):
        if key == "secret_available":
            return "可用" if value else "不可用"
        if key == "configured":
            return "已配置" if value else "未配置"
        if key == "succeeded":
            return "成功" if value else "失败"
        return "已启用" if value else "已停用"
    if isinstance(value, Mapping):
        return f"{len(value)} 项"
    if isinstance(value, (list, tuple, set)):
        if any(isinstance(item, Mapping) for item in value):
            return f"{len(value)} 项"
        return "、".join(map(str, value)) or "—"
    text = str(value)
    return _STATUS_LABELS.get(text.lower(), text)


__all__ = [
    "RESOURCE_LABELS",
    "action_result_renderable",
    "detail_renderable",
    "identity",
    "mapping_renderable",
    "model_catalog_renderable",
    "record_summary",
    "records_renderable",
    "saved_resource_renderable",
    "summary_renderable",
]
