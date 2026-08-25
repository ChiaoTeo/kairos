"""Rich views for Launch and instance results."""

from __future__ import annotations

from typing import Any

from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table


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
    for index, item in enumerate(records, 1):
        table.add_row(
            str(index),
            str(item["launch_id"]),
            str(item.get("mode") or "—"),
            str(item.get("state") or "未运行"),
            str(item.get("instance_id") or "—"),
            "已配置" if item.get("config") else "未找到",
        )
    return Panel(table, title=f"{len(records)} 个运行方案", border_style="cyan")


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
    for index, record in enumerate(records, 1):
        table.add_row(
            str(index),
            str(record.get("instance_id") or "—"),
            str(record.get("mode") or "—"),
            str(record.get("state") or "unknown"),
            str(record.get("created_at") or "—"),
            str(record.get("updated_at") or "—"),
        )
    return Panel(table, title=f"{len(records)} 个运行实例", border_style="cyan")


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
    for index, record in enumerate(records, 1):
        table.add_row(
            str(index),
            str(record.get("component") or "—"),
            str(record.get("status") or "unknown"),
            str(record.get("pid") or "—"),
            str(record.get("control_socket") or record.get("socket") or "—"),
            str(record.get("error") or record.get("detail") or "—"),
        )
    return Panel(table, title=f"{len(records)} 个实例组件", border_style="cyan")


__all__ = ["components_renderable", "instances_renderable", "records_renderable"]
