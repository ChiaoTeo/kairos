"""Workbench-only presentation of System observation results."""

from __future__ import annotations

from typing import Any, Mapping

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from kairospy.system.apps.observe.application import ObserveSnapshot


_COMPONENTS = ("reference", "market", "account", "risk", "execution")


def observe_renderable(snapshot: ObserveSnapshot) -> RenderableType:
    table = Table(show_header=True, header_style="bold")
    table.add_column("组件")
    table.add_column("状态")
    table.add_column("新鲜度")
    table.add_column("详情")
    for name in _COMPONENTS:
        value = snapshot.components.get(name, {})
        table.add_row(
            name,
            str(value.get("status", "unknown")),
            _freshness(value),
            str(value.get("error") or _detail(value)),
        )
    summary = Text(
        f"{snapshot.workspace_id} · {snapshot.overall_status} · "
        f"{len(snapshot.launches)} 个 Launch\n",
        style="bold",
    )
    summary.append(f"建议：{_next_step(snapshot)}", style="dim")
    return Panel(Group(summary, table), title="系统状态", border_style="cyan")


def _next_step(snapshot: ObserveSnapshot) -> str:
    if snapshot.error:
        return "检查工作区诊断信息"
    if not snapshot.launches:
        return "完成工作区检查并配置第一个 Launch"
    latest = snapshot.launches[-1]
    state = str(latest.get("state") or "unknown")
    if state in {"failed", "unresponsive", "degraded"}:
        return "查看最近 Launch 的日志和组件状态"
    if state == "completed":
        return "查看最近 Launch 的运行结果"
    return "继续查看最近 Launch 的运行状态"


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
