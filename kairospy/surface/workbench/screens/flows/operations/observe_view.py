"""Workbench-only presentation of System observation results."""

from __future__ import annotations

from typing import Any, Mapping

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from kairospy.system.apps.observe.application import ObserveSnapshot
from ...presentation import ResultTone, conclusion, section


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
    ready = snapshot.overall_status in {"ready", "healthy"}
    summary = Text(
        f"{snapshot.workspace_id} · {len(snapshot.active_instances)} 个活动实例"
    )
    summary.append(f"\n建议：{_next_step(snapshot)}", style="dim")
    return Group(
        conclusion(
            "系统运行状态已就绪" if ready else "系统存在需要处理的运行状态",
            tone=ResultTone.SUCCESS if ready else ResultTone.WARNING,
        ),
        summary,
        section("组件状态", table),
    )


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
