"""Operator-facing service state and renderables for System Maintenance."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ....widgets import ActionItem
from kairospy.system.apps.observe.application import ObserveSnapshot
from ...selection import SelectionRecord


class ServiceDisplayState(StrEnum):
    RUNNING = "running"
    STARTING = "starting"
    DEGRADED = "degraded"
    STOPPED = "stopped"
    UNRESPONSIVE = "unresponsive"
    STALE = "stale"
    START_FAILED = "start_failed"
    UNKNOWN = "unknown"


_STATE_COPY: dict[ServiceDisplayState, tuple[str, str, str]] = {
    ServiceDisplayState.RUNNING: ("运行中", "green", "服务运行正常"),
    ServiceDisplayState.STARTING: ("启动中", "cyan", "等待服务完成启动"),
    ServiceDisplayState.DEGRADED: ("降级", "yellow", "服务可用，但部分能力异常"),
    ServiceDisplayState.STOPPED: ("已停止", "cyan", "可以启动服务"),
    ServiceDisplayState.UNRESPONSIVE: (
        "无响应",
        "red",
        "进程仍在运行，但控制端点没有响应",
    ),
    ServiceDisplayState.STALE: (
        "资源残留",
        "yellow",
        "进程已经退出，但运行资源尚未清理",
    ),
    ServiceDisplayState.START_FAILED: (
        "启动失败",
        "red",
        "最近一次启动没有成功",
    ),
    ServiceDisplayState.UNKNOWN: ("未知", "grey62", "无法可靠判断服务状态"),
}

_SERVICE_NAMES = {
    "market": "行情服务",
    "reference": "标的服务",
}

_MODE_NAMES = {
    "continuous": "持续运行",
    "on_demand": "按需启动",
    "recovering": "正在恢复",
    "recovery_paused": "恢复已暂停",
    "stopped": "已停止",
}

_SUPPORT_NAMES = {
    "system-supervisor": "System Supervisor",
    "aeron": "Aeron",
}


@dataclass(frozen=True, slots=True)
class SupportStatusView:
    """Typed presentation facts for one System support process."""

    name: str
    status: str
    pid: int | str | None
    pid_alive: bool
    health_file: str | None
    logs_available: bool

    @classmethod
    def from_mapping(cls, name: str, value: Mapping[str, Any]) -> "SupportStatusView":
        return cls(
            name=name,
            status=str(value.get("status") or "unknown"),
            pid=value.get("pid"),
            pid_alive=bool(value.get("pid_alive")),
            health_file=(
                str(value["health_file"]) if value.get("health_file") else None
            ),
            logs_available=bool(value.get("logs_available")),
        )


def operations_records(snapshot: ObserveSnapshot) -> tuple[SelectionRecord, ...]:
    """Map one System snapshot into the selectable product hierarchy."""

    records: list[SelectionRecord] = []
    for component in ("reference", "market"):
        raw = {
            "component": component,
            **snapshot.shared_services.get(component, {}),
        }
        view = service_status_view(raw)
        mode = _MODE_NAMES.get(str(raw.get("operating_mode")), "运行方式未知")
        records.append(
            SelectionRecord(
                f"service:{component}",
                view.display_name,
                f"{view.state_label} · {mode}",
                {"kind": "service", "value": view},
            )
        )
    for instance in snapshot.active_instances:
        state = str(instance.get("state") or "unknown")
        launch_id = str(instance.get("launch_id") or "未命名运行方案")
        instance_id = str(instance.get("instance_id") or "—")
        mode = str(instance.get("mode") or "—")
        records.append(
            SelectionRecord(
                f"run-instance:{launch_id}:{instance_id}",
                f"{launch_id} / {instance_id}",
                f"{mode} · {state}",
                {"kind": "run-instance", "value": dict(instance)},
            )
        )
    for name in ("system-supervisor", "aeron"):
        raw = dict(snapshot.support_processes.get(name, {}))
        view = SupportStatusView.from_mapping(name, raw)
        status = str(raw.get("status") or "unknown")
        label = "运行中" if status == "running" else "已停止"
        records.append(
            SelectionRecord(
                f"support:{name}",
                _SUPPORT_NAMES[name],
                label,
                {"kind": "support", "name": name, "value": view},
            )
        )
    return tuple(records)


def operations_group_records(
    records: tuple[SelectionRecord, ...],
) -> tuple[SelectionRecord, ...]:
    """Group the runtime inventory by product scope before object selection."""

    groups = (
        ("services", "项目共享服务", "service", "个服务"),
        ("instances", "活动运行实例", "run-instance", "个实例"),
        ("supports", "支撑进程", "support", "个进程"),
    )
    grouped: list[SelectionRecord] = []
    for key, label, kind, unit in groups:
        children = tuple(
            record
            for record in records
            if isinstance(record.value, Mapping) and record.value.get("kind") == kind
        )
        description = f"{len(children)} {unit}" if children else "当前没有运行对象"
        grouped.append(
            SelectionRecord(
                f"operations-group:{key}",
                label,
                description,
                {"kind": "group", "name": key, "records": children},
            )
        )
    return tuple(grouped)


def operations_overview(snapshot: ObserveSnapshot) -> RenderableType:
    state = {
        "healthy": ("正常", "green"),
        "partial": ("部分就绪", "yellow"),
        "degraded": ("存在异常", "red"),
    }.get(snapshot.overall_status, (snapshot.overall_status, "yellow"))
    body = Text()
    body.append(f"当前项目：{snapshot.workspace_id}\n", style="bold")
    body.append("整体状态：")
    body.append(state[0], style=f"bold {state[1]}")
    return body


def project_result_renderable(action_name: str, result: object) -> RenderableType:
    """Present project outcomes as operator decisions instead of Python data."""

    if not isinstance(result, Mapping):
        return Panel(Text(str(result) or "操作已完成"), title="项目操作")
    if action_name.endswith(".doctor"):
        return _project_doctor_renderable(result)

    table = Table.grid(padding=(0, 3))
    table.add_column(style="dim", no_wrap=True)
    table.add_column()
    if action_name.endswith(".status"):
        table.add_row("当前项目", str(result.get("workspace_id") or "—"))
        table.add_row("项目目录", str(result.get("project_root") or "—"))
        table.add_row("工作目录", str(result.get("workspace_root") or "—"))
        table.add_row("状态", Text("项目已打开", style="bold green"))
        return Panel(table, title="项目概览", border_style="cyan")

    status = str(result.get("status") or "completed")
    title, label = {
        "opened": ("项目已切换", "已打开新项目"),
        "initialized": ("项目已创建", "新项目可以使用"),
        "scaffolded": ("模板已安装", "项目模板安装完成"),
        "preview": ("项目操作预览", "尚未写入任何内容"),
    }.get(status, ("项目操作完成", "操作已完成"))
    table.add_row("结果", Text(label, style="bold green"))
    for key, label in (
        ("workspace_id", "项目名称"),
        ("project_root", "项目目录"),
        ("workspace_root", "工作目录"),
        ("template", "项目模板"),
        ("action", "计划操作"),
        ("root", "目标目录"),
    ):
        value = result.get(key)
        if value is not None and value != "":
            table.add_row(label, str(value))
    created = result.get("created")
    if isinstance(created, (list, tuple)):
        table.add_row("新增文件", f"{len(created)} 个")
    return Panel(table, title=title, border_style="cyan")


def _project_doctor_renderable(report: Mapping[str, Any]) -> RenderableType:
    ready = bool(report.get("ready"))
    ok = bool(report.get("ok"))
    issues = _string_items(report.get("issues"))
    missing = _string_items(report.get("missing_directories"))
    launches = report.get("launches")
    launch_count = len(launches) if isinstance(launches, (list, tuple)) else 0
    groups: dict[tuple[str, str], set[str]] = {}
    for issue in issues:
        launch, detail = _split_launch_issue(issue)
        label, resource = _classify_project_issue(detail)
        groups.setdefault((label, resource), set()).add(launch)
    if missing:
        groups[("目录缺失", f"{len(missing)} 个必要目录")] = {"项目"}

    summary = Table.grid(padding=(0, 3))
    summary.add_column(style="dim", no_wrap=True)
    summary.add_column()
    if ready:
        conclusion = Text("可以运行", style="bold green")
    elif ok:
        conclusion = Text("结构正常，但没有可运行方案", style="bold yellow")
    else:
        conclusion = Text("尚未就绪", style="bold red")
    summary.add_row("检查结论", conclusion)
    summary.add_row("运行方案", f"{launch_count} 个")
    summary.add_row("待处理", f"{len(groups)} 类问题" if groups else "没有发现问题")

    sections: list[RenderableType] = [summary]
    if groups:
        problem_table = Table(show_header=True, header_style="bold")
        problem_table.add_column("问题")
        problem_table.add_column("资源")
        problem_table.add_column("影响")
        for (label, resource), affected in groups.items():
            named = sorted(name for name in affected if name != "项目")
            impact = f"{len(named)} 个运行方案" if named else "当前项目"
            problem_table.add_row(label, resource, impact)
        sections.extend((Text(), problem_table))

        next_steps = Text()
        next_steps.append("\n建议下一步\n", style="bold cyan")
        for index, step in enumerate(_project_next_steps(groups), 1):
            next_steps.append(f"{index}. {step}\n")
        next_steps.append(
            "\n技术详情：kairos project doctor --format json",
            style="dim",
        )
        sections.append(next_steps)
    elif ready:
        sections.extend((Text(), Text("项目检查通过。", style="green")))
    else:
        next_step = Text()
        next_step.append("项目结构正常；当前没有可运行方案。\n", style="yellow")
        next_step.append("\n建议下一步\n", style="bold cyan")
        next_step.append("1. 安装项目模板或创建一个运行方案。\n")
        next_step.append(
            "\n技术详情：kairos project doctor --format json",
            style="dim",
        )
        sections.extend((Text(), next_step))
    return Panel(Group(*sections), title="项目检查", border_style="cyan")


def _string_items(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value)


def _split_launch_issue(issue: str) -> tuple[str, str]:
    match = re.match(r"launch ([^:]+): (.*)", issue)
    return (match.group(1), match.group(2)) if match else ("项目", issue)


def _classify_project_issue(detail: str) -> tuple[str, str]:
    match = re.search(r"Workspace data connection is unavailable: ([^:]+)", detail)
    if match:
        return "市场数据连接缺失", match.group(1).strip(" '\"")
    match = re.search(r"successful manual connection test: ([^']+)", detail)
    if match:
        return "账户连接尚未验证", match.group(1).strip(" '\"")
    match = re.search(r"account ['\"]?([^'\"]+)['\"]? is not configured", detail)
    if match:
        return "交易账户未配置", match.group(1).strip()
    if "replay file does not exist" in detail:
        return "回放数据缺失", detail.rsplit(":", 1)[-1].strip()
    if "workspace manifest" in detail:
        return "项目 Manifest 无效", "manifest.toml"
    return "运行方案配置异常", _short_issue(detail)


def _short_issue(detail: str) -> str:
    return detail if len(detail) <= 56 else detail[:53] + "…"


def _project_next_steps(groups: Mapping[tuple[str, str], set[str]]) -> tuple[str, ...]:
    labels = {label for label, _ in groups}
    steps: list[str] = []
    if "市场数据连接缺失" in labels:
        steps.append("进入运行资源，配置并测试缺失的市场数据连接。")
    if labels & {"账户连接尚未验证", "交易账户未配置"}:
        steps.append("进入交易账户，补充配置并完成手动连接测试。")
    if "回放数据缺失" in labels:
        steps.append("补充运行方案引用的回放数据文件。")
    if labels & {"项目 Manifest 无效", "目录缺失", "运行方案配置异常"}:
        steps.append("修复项目结构或运行方案配置后重新检查。")
    steps.append("处理完成后再次运行“检查项目”。")
    return tuple(steps)


def support_summary(view: SupportStatusView) -> RenderableType:
    table = Table.grid(padding=(0, 3))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("状态", "运行中" if view.status == "running" else "已停止")
    table.add_row("进程", str(view.pid or "无"))
    table.add_row("日志", "可用" if view.logs_available else "尚未生成")
    table.add_row("说明", "支撑进程只提供状态和技术证据，不提供普通服务启停。")
    return Group(
        Text(_SUPPORT_NAMES.get(view.name, view.name), style="bold cyan"),
        Text(),
        table,
    )


def support_diagnostics(view: SupportStatusView) -> RenderableType:
    table = Table.grid(padding=(0, 3))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("PID", str(view.pid or "—"))
    table.add_row("进程存活", "是" if view.pid_alive else "否")
    table.add_row("Health file", view.health_file or "—")
    table.add_row("日志可用", "是" if view.logs_available else "否")
    return Group(
        Text(f"{_SUPPORT_NAMES.get(view.name, view.name)}技术证据", style="bold cyan"),
        Text(),
        table,
    )


def service_display_name(component: str) -> str:
    """Return the operator-facing name while preserving the runtime identifier."""

    return _SERVICE_NAMES.get(component, component)


def service_status_line(view: ServiceStatusView) -> str:
    """Return the compact lifecycle context shown above the command input."""

    parts = [view.display_name, view.state_label]
    dependents = view.raw.get("dependents")
    if isinstance(dependents, (list, tuple)):
        parts.append(
            f"{len(dependents)} 个活动实例正在使用" if dependents else "无活动运行实例"
        )
    return " · ".join(parts)


@dataclass(frozen=True, slots=True)
class ServiceStatusView:
    """Presentation-only interpretation of one component status response."""

    component: str
    state: ServiceDisplayState
    raw: Mapping[str, Any]
    pid: int | str | None
    logs_available: bool
    recommendation: str

    @property
    def state_label(self) -> str:
        return _STATE_COPY[self.state][0]

    @property
    def state_style(self) -> str:
        return _STATE_COPY[self.state][1]

    @property
    def summary(self) -> str:
        return _STATE_COPY[self.state][2]

    @property
    def display_name(self) -> str:
        return service_display_name(self.component)


def service_status_view(value: object) -> ServiceStatusView:
    """Convert raw application output once at the Workbench boundary."""

    if isinstance(value, ServiceStatusView):
        return value
    if not isinstance(value, Mapping):
        value = {"component": str(value), "status": "unknown"}
    raw = dict(value)
    component = str(raw.get("component") or "unknown")
    state = _display_state(raw)
    log_path = raw.get("log_file")
    logs_available = bool(raw.get("logs_available")) or bool(
        log_path and Path(str(log_path)).is_file()
    )
    recommendation = {
        ServiceDisplayState.RUNNING: "服务运行正常；可跟随日志或刷新状态。",
        ServiceDisplayState.STARTING: "等待服务就绪；必要时查看启动日志。",
        ServiceDisplayState.DEGRADED: "查看技术诊断和日志，确认受影响能力。",
        ServiceDisplayState.STOPPED: f"启动 {service_display_name(component)}。",
        ServiceDisplayState.UNRESPONSIVE: "先查看日志；确认后可安全停止服务。",
        ServiceDisplayState.STALE: "清理失效资源后重新启动服务。",
        ServiceDisplayState.START_FAILED: "查看启动日志后重新启动服务。",
        ServiceDisplayState.UNKNOWN: "重新检查状态并查看技术诊断。",
    }[state]
    return ServiceStatusView(
        component=component,
        state=state,
        raw=raw,
        pid=raw.get("pid"),
        logs_available=logs_available,
        recommendation=recommendation,
    )


def _display_state(value: Mapping[str, Any]) -> ServiceDisplayState:
    status = str(value.get("status") or "unknown").lower()
    if status in {"ok", "ready", "running", "healthy"}:
        return ServiceDisplayState.RUNNING
    if status in {"starting", "initializing"}:
        return ServiceDisplayState.STARTING
    if status in {"degraded", "unhealthy"}:
        return ServiceDisplayState.DEGRADED
    if status in {"unresponsive", "timeout"} or (
        bool(value.get("pid_alive")) and value.get("control_reachable") is False
    ):
        return ServiceDisplayState.UNRESPONSIVE
    if status in {"failed", "start_failed", "error"}:
        return ServiceDisplayState.START_FAILED
    if status == "stale" or (
        not bool(value.get("pid_alive"))
        and bool(value.get("control_socket_exists"))
        and value.get("control_reachable") is False
    ):
        return ServiceDisplayState.STALE
    if status in {"not_running", "stopped"}:
        return ServiceDisplayState.STOPPED
    return ServiceDisplayState.UNKNOWN


def service_actions(view: ServiceStatusView | None) -> tuple[ActionItem, ...]:
    """Return only actions that make sense for the current lifecycle state."""

    if view is None:
        return ()

    def action(action_id: str, label: str, detail: str, shortcut: int) -> ActionItem:
        return ActionItem(action_id, label, detail, str(shortcut))

    def refresh(shortcut: int) -> ActionItem:
        return action("status", "刷新状态", "重新检查进程与健康状态", shortcut)

    def recent(shortcut: int) -> ActionItem:
        return action("logs", "查看最近日志", "读取最近 200 行进程日志", shortcut)

    def follow(shortcut: int) -> ActionItem:
        return action("follow", "跟随实时日志", "在内容区持续显示新增日志", shortcut)

    def diagnostics(shortcut: int) -> ActionItem:
        return action(
            "diagnostics",
            "查看技术诊断",
            "检查进程、控制端点和运行资源",
            shortcut,
        )

    if view.state is ServiceDisplayState.STOPPED:
        return (
            action("start", "启动并保持运行", "启动服务并登记自动恢复", 1),
            recent(2),
            follow(3),
            diagnostics(4),
        )
    if view.state is ServiceDisplayState.STALE:
        return (
            ActionItem("repair-start", "清理并启动", "清理确认失效的资源后启动", "1"),
            ActionItem("repair", "仅清理失效资源", "保留服务停止状态", "2"),
            recent(3),
            diagnostics(4),
        )
    if view.state in {ServiceDisplayState.RUNNING, ServiceDisplayState.DEGRADED}:
        return (
            refresh(1),
            action("stop", "停止", "请求组件安全停止", 2),
            action("restart", "重启", "停止后启动新的组件进程", 3),
            recent(4),
            follow(5),
            diagnostics(6),
        )
    if view.state is ServiceDisplayState.UNRESPONSIVE:
        return (
            refresh(1),
            action("stop", "停止", "请求组件安全停止", 2),
            recent(3),
            follow(4),
            diagnostics(5),
        )
    if view.state is ServiceDisplayState.STARTING:
        return refresh(1), recent(2), follow(3), diagnostics(4)
    if view.state is ServiceDisplayState.START_FAILED:
        return (
            action("start", "重新启动", "重新启动组件并等待就绪", 1),
            recent(2),
            follow(3),
            diagnostics(4),
        )
    return refresh(1), recent(2), diagnostics(3)


LOG_FOLLOW_ACTIONS = (
    ActionItem("refresh", "立即刷新", "读取当前新增日志", "1"),
    ActionItem("pause", "暂停或继续", "控制后台日志刷新", "p"),
    ActionItem("clear", "清空当前窗口", "不删除完整日志文件", "c"),
)

SUPPORT_ACTIONS = (
    ActionItem("refresh", "刷新运行结构", "重新读取支撑进程状态", "1"),
    ActionItem("logs", "查看最近日志", "读取最近 200 行进程日志", "2"),
    ActionItem("diagnostics", "查看技术证据", "查看 PID、Health file 和日志位置", "3"),
)


def service_summary(view: ServiceStatusView) -> RenderableType:
    table = Table.grid(padding=(0, 3))
    table.add_column(style="dim", no_wrap=True)
    table.add_column()
    state = Text(view.state_label, style=f"bold {view.state_style}")
    table.add_row("状态", state)
    table.add_row("说明", view.summary)
    mode = _MODE_NAMES.get(str(view.raw.get("operating_mode")))
    if mode is not None:
        table.add_row("运行方式", mode)
    dependents = view.raw.get("dependents")
    if isinstance(dependents, (list, tuple)):
        labels = [
            f"{item.get('launch_id')} / {item.get('instance_id')}"
            for item in dependents
            if isinstance(item, Mapping)
        ]
        table.add_row("正在使用", "、".join(labels) if labels else "无活动运行实例")
    table.add_row("进程", str(view.pid) if view.pid is not None else "无")
    table.add_row("日志", "可用" if view.logs_available else "尚未生成")
    table.add_row("建议", Text(view.recommendation, style="bold"))
    return Group(Text(view.display_name, style="bold cyan"), Text(), table)


def services_overview(views: tuple[ServiceStatusView, ...]) -> RenderableType:
    table = Table.grid(padding=(0, 3))
    table.add_column(style="bold", no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column()
    for view in views:
        table.add_row(
            view.display_name,
            Text(view.state_label, style=f"bold {view.state_style}"),
            view.summary,
        )
    needs_action = tuple(
        view
        for view in views
        if view.state not in {ServiceDisplayState.RUNNING, ServiceDisplayState.STOPPED}
    )
    if not needs_action:
        advice = Text("所有后台服务状态明确；可选择组件进行控制。", style="dim")
    else:
        advice = Text(
            "建议：" + "；".join(view.recommendation for view in needs_action),
            style="yellow",
        )
    return Group(table, Text(), advice)


def diagnostics_renderable(view: ServiceStatusView) -> RenderableType:
    raw = view.raw
    table = Table.grid(padding=(0, 3))
    table.add_column(style="dim", no_wrap=True)
    table.add_column()
    fields = (
        ("PID", raw.get("pid") or "—"),
        ("进程存活", "是" if raw.get("pid_alive") else "否"),
        ("进程命令", raw.get("process_command") or "—"),
        ("控制端点", raw.get("control_socket") or "—"),
        ("控制可达", "是" if raw.get("control_reachable") else "否"),
        ("探测错误", raw.get("probe_error") or raw.get("error") or "—"),
        ("Health file", raw.get("health_file") or "—"),
        ("Process lock", raw.get("process_lock") or "—"),
        ("Lock held", "是" if raw.get("process_lock_held") else "否"),
    )
    for label, value in fields:
        table.add_row(label, str(value))
    return Group(
        Text(f"{view.display_name}技术诊断", style="bold cyan"),
        Text(),
        table,
        Text(),
        Text(f"结论：{view.summary}。{view.recommendation}", style=view.state_style),
    )


__all__ = [
    "LOG_FOLLOW_ACTIONS",
    "SUPPORT_ACTIONS",
    "ServiceDisplayState",
    "ServiceStatusView",
    "SupportStatusView",
    "diagnostics_renderable",
    "service_actions",
    "service_display_name",
    "service_status_view",
    "service_status_line",
    "service_summary",
    "services_overview",
    "operations_overview",
    "operations_group_records",
    "operations_records",
    "support_summary",
    "support_diagnostics",
]
