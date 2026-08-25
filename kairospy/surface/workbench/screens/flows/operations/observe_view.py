"""Workbench-only presentation of System observation results."""

from __future__ import annotations

from typing import Any, Mapping

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from kairospy.system.apps.observe.application import ObserveSnapshot


_SHARED_SERVICES = ("reference", "market")


def observe_renderable(snapshot: ObserveSnapshot) -> RenderableType:
    table = Table(show_header=True, header_style="bold")
    table.add_column("组件")
    table.add_column("状态")
    table.add_column("新鲜度")
    table.add_column("详情")
    for name in _SHARED_SERVICES:
        value = snapshot.shared_services.get(name, {})
        table.add_row(
            name,
            str(value.get("status", "unknown")),
            _freshness(value),
            str(value.get("error") or _detail(value)),
        )
    summary = Text(
        f"{snapshot.workspace_id} · {snapshot.overall_status} · "
        f"{len(snapshot.active_instances)} 个活动实例\n",
        style="bold",
    )
    summary.append(f"建议：{_next_step(snapshot)}", style="dim")
    return Panel(Group(summary, table), title="系统状态", border_style="cyan")


def _next_step(snapshot: ObserveSnapshot) -> str:
    if snapshot.error:
        return "检查工作区诊断信息"
    if not snapshot.active_instances:
        return "当前没有活动运行实例"
    latest = snapshot.active_instances[-1]
    state = str(latest.get("state") or "unknown")
    if state in {"failed", "unresponsive", "degraded"}:
        return "查看异常运行实例的日志和组件状态"
    return "继续查看活动运行实例的状态"


def _freshness(value: Mapping[str, Any]) -> str:
    for key in ("last_event_age_ms", "freshness_age_ms", "age_ms"):
        if value.get(key) is not None:
            try:
                return f"{float(value[key]) / 1000:.1f}s ago"
            except (TypeError, ValueError):
                pass
    for key in ("freshness", "data_health", "readiness"):
        if value.get(key) is not None:
            return str(value[key])
    return "-"


def _detail(value: Mapping[str, Any]) -> str:
    return f"pid={value['pid']}" if value.get("pid") else "-"


__all__ = ["observe_renderable"]
