"""Rich views for Launch and instance results."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ...presentation import conclusion, section


def records_renderable(records: tuple[dict[str, Any], ...]) -> RenderableType:
    if not records:
        return Panel("没有运行方案。输入 /new 开始创建。", title="运行方案")
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right", style="bold cyan")
    table.add_column("运行方案")
    table.add_column("模式")
    table.add_column("状态")
    table.add_column("实例")
    table.add_column("配置")
    for index, item in enumerate(records[:20], 1):
        table.add_row(
            str(index),
            str(item["launch_id"]),
            str(item.get("mode") or "—"),
            str(item.get("state") or "未运行"),
            str(item.get("instance_id") or "—"),
            "已配置" if item.get("config") else "未找到",
        )
    return _collection("运行方案", records, table)


def instances_renderable(records: tuple[dict[str, Any], ...]) -> RenderableType:
    if not records:
        return Panel("没有已注册实例。", title="运行实例")
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right", style="bold cyan")
    table.add_column("Instance")
    table.add_column("模式")
    table.add_column("状态")
    table.add_column("创建时间")
    table.add_column("更新时间")
    for index, record in enumerate(records[:20], 1):
        table.add_row(
            str(index),
            str(record.get("instance_id") or "—"),
            str(record.get("mode") or "—"),
            str(record.get("state") or "unknown"),
            str(record.get("created_at") or "—"),
            str(record.get("updated_at") or "—"),
        )
    return _collection("运行实例", records, table)


def components_renderable(records: tuple[dict[str, Any], ...]) -> RenderableType:
    if not records:
        return Panel("没有实例组件。", title="实例组件")
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right", style="bold cyan")
    table.add_column("组件")
    table.add_column("状态")
    table.add_column("PID")
    table.add_column("Socket")
    table.add_column("详情")
    for index, record in enumerate(records[:20], 1):
        table.add_row(
            str(index),
            str(record.get("component") or "—"),
            str(record.get("status") or "unknown"),
            str(record.get("pid") or "—"),
            str(record.get("control_socket") or record.get("socket") or "—"),
            str(record.get("error") or record.get("detail") or "—"),
        )
    return _collection("实例组件", records, table)


def _collection(title: str, records: tuple[dict[str, Any], ...], table: Table) -> Group:
    visible = min(len(records), 20)
    return Group(
        conclusion(f"{title}共 {len(records)} 条记录"),
        section(title, table),
        Text(
            f"显示 {visible} 条 · 其余 {len(records) - visible} 条"
            if len(records) > visible
            else f"共 {len(records)} 条",
            style="dim",
        ),
    )


def attach_renderable(value: Mapping[str, object]) -> RenderableType:
    """Render platform-owned Strategy runtime diagnostics for Launch Attach."""

    raw_status = value.get("status")
    if not isinstance(raw_status, Mapping):
        return Panel(str(raw_status or "等待运行状态…"), title="Strategy Runtime")
    status = raw_status
    summary = Table.grid(expand=True)
    summary.add_column(style="bold cyan", no_wrap=True)
    summary.add_column()
    summary.add_column(style="bold cyan", no_wrap=True)
    summary.add_column()
    summary.add_row(
        "生命周期",
        _field(status, "strategy_status", "status"),
        "就绪",
        _field(status, "readiness"),
    )
    summary.add_row(
        "数据健康",
        _field(status, "data_health"),
        "事件",
        f"{_field(status, 'event_count', fallback='0')} · {_field(status, 'last_event_kind')}",
    )
    summary.add_row(
        "订阅",
        f"{_field(status, 'active_subscription_count', fallback='0')}/"
        f"{_field(status, 'subscription_count', fallback='0')} active",
        "最近事件",
        _field(status, "last_event_age_ms", fallback="—", suffix=" ms"),
    )

    components = Table(show_header=True, header_style="bold")
    components.add_column("组件")
    components.add_column("状态")
    components.add_column("PID")
    components.add_column("详情", overflow="fold")
    raw_components = status.get("component_status")
    if isinstance(raw_components, Mapping):
        for name in ("reference", "market", "strategy"):
            raw_component = raw_components.get(name)
            if not isinstance(raw_component, Mapping):
                continue
            components.add_row(
                name.capitalize(),
                _field(raw_component, "status"),
                _field(raw_component, "pid"),
                _field(raw_component, "error", "detail"),
            )
    if components.row_count == 0:
        components.add_row("Strategy", _field(status, "status"), "—", "—")

    subscriptions = Table(show_header=True, header_style="bold")
    subscriptions.add_column("状态")
    subscriptions.add_column("目标", overflow="fold")
    subscriptions.add_column("数据")
    subscriptions.add_column("Providers")
    raw_subscriptions = status.get("subscriptions")
    if isinstance(raw_subscriptions, list):
        for raw_subscription in raw_subscriptions:
            if not isinstance(raw_subscription, Mapping):
                continue
            response = raw_subscription.get("response")
            response_map = response if isinstance(response, Mapping) else {}
            subscriptions.add_row(
                _field(raw_subscription, "status"),
                _compact(raw_subscription.get("target")),
                _compact(raw_subscription.get("observations")),
                _compact(response_map.get("resolved_providers")),
            )
    if subscriptions.row_count == 0:
        subscriptions.add_row("—", "没有 Market 订阅", "—", "—")

    streams = Table(show_header=True, header_style="bold")
    streams.add_column("流")
    streams.add_column("Scope", overflow="fold")
    streams.add_column("Provider")
    streams.add_column("事件", justify="right")
    streams.add_column("序列", justify="right")
    raw_streams = status.get("streams")
    if isinstance(raw_streams, list):
        for raw_stream in raw_streams:
            if not isinstance(raw_stream, Mapping):
                continue
            streams.add_row(
                _field(raw_stream, "kind", "domain"),
                _field(raw_stream, "scope"),
                _field(raw_stream, "provider"),
                _field(raw_stream, "event_count", fallback="0"),
                _field(raw_stream, "last_source_sequence"),
            )
    if streams.row_count == 0:
        streams.add_row("—", "等待数据", "—", "0", "—")

    recent = Table(show_header=True, header_style="bold")
    recent.add_column("#", justify="right")
    recent.add_column("事件")
    recent.add_column("时间")
    recent.add_column("数据", overflow="fold")
    raw_recent = status.get("recent_events")
    if isinstance(raw_recent, list):
        for raw_event in raw_recent[-5:]:
            if not isinstance(raw_event, Mapping):
                continue
            recent.add_row(
                _field(raw_event, "source_sequence"),
                _field(raw_event, "kind"),
                _field(raw_event, "event_time"),
                _compact(raw_event.get("summary")),
            )
    if recent.row_count == 0:
        recent.add_row("—", "等待首个事件", "—", "—")

    notification = status.get("market_notification")
    notification_text = (
        Text("Market 通知：尚未建立", style="dim")
        if not isinstance(notification, Mapping)
        else Text(
            "Market 通知："
            f"cursor={_field(notification, 'cursor')} "
            f"gap={_field(notification, 'gap_count', fallback='0')} "
            f"incarnation changes={_field(notification, 'incarnation_change_count', fallback='0')}",
            style="dim",
        )
    )
    return Group(
        conclusion("Strategy 运行输出已刷新"),
        section("Strategy Runtime", summary),
        section("内置连接", components),
        section("Market 订阅", subscriptions),
        section("数据流", streams),
        section("最近数据", recent),
        notification_text,
    )


def _field(
    value: Mapping[str, object],
    *names: str,
    fallback: str = "—",
    suffix: str = "",
) -> str:
    for name in names:
        raw = value.get(name)
        if raw is not None and raw != "":
            return f"{raw}{suffix}"
    return fallback


def _compact(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


__all__ = [
    "attach_renderable",
    "components_renderable",
    "instances_renderable",
    "records_renderable",
]
