"""Launch discovery and control actions for the Workbench product slice."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from kairospy.system.apps.launch.application import (
    LaunchConfigurationApplication,
    LaunchInstanceTimelineApplication,
    LaunchRegistryApplication,
    LaunchRuntimeApplication,
)
from kairospy.system.apps.launch.application.wizard import (
    LaunchDraft,
    build_and_validate,
    draft_preview,
    load_values,
)
from kairospy.system.apps.components.application.supervisor import UnixRestClient

from ....widgets import ActionItem
from .views import (
    components_renderable,
    instances_renderable,
    records_renderable,
)
from .support import config_path as _config_path
from .support import owner as _owner
from .wizard import (
    LaunchWizardState,
    open_edit_launch_wizard,
    open_new_launch_wizard,
    save_launch_wizard,
)


class LaunchAction(StrEnum):
    """Actions owned by a reusable run plan."""

    START = "start"
    VALIDATE = "validate"
    INSTANCES = "instances"
    EDIT = "edit"
    CONFIG = "config"


class InstanceAction(StrEnum):
    """Actions owned by one concrete run instance."""

    OVERVIEW = "overview"
    ATTACH = "attach"
    COMPONENTS = "components"
    TIMELINE = "timeline"
    REPORT = "report"
    WAIT = "wait"
    STOP = "stop"
    RESTART = "restart"


class AttachAction(StrEnum):
    """Controls owned by the live instance-output view."""

    REFRESH = "refresh"
    PAUSE = "pause"
    CLEAR = "clear"
    PYTHON = "python"


class TimelineAction(StrEnum):
    """Actions owned by one instance timeline."""

    REFRESH = "refresh"
    EXPORT = "export"


class ReadinessAction(StrEnum):
    """Recovery actions derived from one run-plan readiness report."""

    RETRY = "retry"
    ACCOUNTS = "resource-accounts"
    DATA = "resource-data"
    MODELS = "resource-models"
    NOTIFICATIONS = "resource-notifications"
    EDIT = "edit"


@dataclass(frozen=True, slots=True)
class LaunchReadinessDiagnostic:
    owner: str
    resource: str
    severity: str
    reason: str
    action: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> LaunchReadinessDiagnostic:
        return cls(
            owner=str(value.get("owner") or "Launch"),
            resource=str(value.get("resource") or "launch"),
            severity=str(value.get("severity") or "blocker"),
            reason=str(value.get("reason") or "运行条件尚未满足"),
            action=str(value.get("action") or "检查并修正运行方案"),
        )


@dataclass(frozen=True, slots=True)
class LaunchReadinessView:
    valid: bool
    path: str
    diagnostics: tuple[LaunchReadinessDiagnostic, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> LaunchReadinessView:
        diagnostics = tuple(
            LaunchReadinessDiagnostic.from_mapping(item)
            for item in value.get("diagnostics", ())
            if isinstance(item, Mapping)
        )
        return cls(
            valid=value.get("valid") is True,
            path=str(value.get("path") or ""),
            diagnostics=diagnostics,
        )


def readiness_actions(view: LaunchReadinessView | None) -> tuple[ActionItem, ...]:
    """Project actionable owner fixes without copying their configuration UI."""

    resources = {
        diagnostic.resource for diagnostic in (view.diagnostics if view else ())
    }
    actions = [
        ActionItem(
            ReadinessAction.RETRY, "重新校验运行条件", "修复后重新读取全部条件", "1"
        )
    ]
    fixes = (
        (
            ReadinessAction.ACCOUNTS,
            {"accounts"},
            "修复交易账户",
            "配置并验证运行方案引用的账户",
        ),
        (
            ReadinessAction.DATA,
            {"data_provider"},
            "修复行情连接",
            "配置并验证运行方案使用的市场数据连接",
        ),
        (
            ReadinessAction.MODELS,
            {"model_connection", "agent"},
            "修复 AI 模型连接",
            "配置并验证 Agent 使用的模型",
        ),
        (
            ReadinessAction.NOTIFICATIONS,
            {"destinations", "notifications"},
            "修复通知连接",
            "配置并验证运行方案使用的通知目标",
        ),
    )
    for action, owned_resources, label, description in fixes:
        if resources & owned_resources:
            actions.append(
                ActionItem(action, label, description, str(len(actions) + 1))
            )
    actions.append(
        ActionItem(
            ReadinessAction.EDIT,
            "编辑运行方案",
            "修正风险、执行、Market 或其他方案内条件",
            str(len(actions) + 1),
        )
    )
    return tuple(actions)


LAUNCH_ACTIONS = (
    ActionItem(
        LaunchAction.START, "新建运行实例", "按这个方案开始一次新的实际运行", "1"
    ),
    ActionItem(
        LaunchAction.VALIDATE, "校验运行条件", "检查方案结构、连接资源和启动条件", "2"
    ),
    ActionItem(
        LaunchAction.INSTANCES, "查看运行实例", "选择当前或历史上的一次实际运行", "3"
    ),
    ActionItem(
        LaunchAction.EDIT, "编辑运行方案", "逐项修改这份可重复使用的运行配置", "4"
    ),
    ActionItem(
        LaunchAction.CONFIG, "查看运行方案", "解释规范化配置和实际运行计划", "5"
    ),
)


def instance_actions(record: Mapping[str, Any] | None) -> tuple[ActionItem, ...]:
    """Return actions for one concrete run instance, never for its plan."""

    value = record or {}
    state = str(value.get("state") or value.get("status") or "").casefold()
    mode = str(value.get("mode") or "").casefold()
    terminal = state in {
        "cancelled",
        "completed",
        "failed",
        "finished",
        "stopped",
        "terminated",
    }
    actions = [
        ActionItem(
            InstanceAction.OVERVIEW,
            "查看实例状态",
            "读取这次运行的整体状态和注册信息",
            "1",
        ),
        ActionItem(
            InstanceAction.ATTACH,
            "查看运行输出",
            "持续查看这次运行的状态和策略日志",
            "2",
        ),
        ActionItem(
            InstanceAction.COMPONENTS,
            "查看实例组件",
            "查看行情、执行、风控等实例组件",
            "3",
        ),
        ActionItem(
            InstanceAction.TIMELINE,
            "查看实例时间线",
            "查看这次运行的生命周期审计记录",
            "4",
        ),
    ]
    if mode == "backtest":
        actions.append(
            ActionItem(
                InstanceAction.REPORT if terminal else InstanceAction.WAIT,
                "查看回测报告" if terminal else "等待并查看回测报告",
                "读取已经完成的回测结果" if terminal else "等待这次回测完成并读取报告",
                "5",
            )
        )
    if not terminal:
        actions.append(
            ActionItem(
                InstanceAction.STOP,
                "停止这个实例",
                "停止这次运行并释放它占用的资源",
                str(len(actions) + 1),
            )
        )
    actions.append(
        ActionItem(
            InstanceAction.RESTART,
            "从同一方案重新运行",
            "停止当前实例并创建一个具有新身份的实例",
            str(len(actions) + 1),
        )
    )
    return tuple(actions)


ATTACH_ACTIONS = (
    ActionItem(AttachAction.REFRESH, "刷新运行输出", "读取当前状态与最近日志", "1"),
    ActionItem(AttachAction.PAUSE, "暂停或继续", "控制后台运行输出刷新", "2"),
    ActionItem(
        AttachAction.CLEAR, "清空当前窗口", "只清除当前可见日志，不删除日志源", "3"
    ),
    ActionItem(
        AttachAction.PYTHON, "发送 Strategy Python", "向当前 Strategy 提交一行代码", "4"
    ),
)

TIMELINE_ACTIONS = (
    ActionItem(
        TimelineAction.REFRESH, "刷新时间线", "读取最近 200 条生命周期记录", "1"
    ),
    ActionItem(TimelineAction.EXPORT, "导出 JSONL", "写入指定的时间线导出文件", "2"),
)


def load_launches(state: Any) -> tuple[dict[str, Any], ...]:
    owner = _owner(state)
    by_id: dict[str, dict[str, Any]] = {}
    for entry in LaunchRegistryApplication(owner).list():
        launch_id = str(entry.get("launch_id") or "")
        if launch_id:
            by_id[launch_id] = dict(entry)
    config_root = owner.paths.launch_config("_").parent
    for path in sorted(config_root.glob("*.toml")):
        launch_id = path.stem
        entry = by_id.setdefault(launch_id, {"launch_id": launch_id})
        entry.setdefault("config", str(path))
        try:
            config = LaunchConfigurationApplication().load(
                path, workspace_root=owner.paths.root
            )
            entry.setdefault("mode", config.mode)
        except (OSError, ValueError):
            entry.setdefault("mode", "—")
    for draft in LaunchConfigurationApplication().list_drafts(owner.paths.root):
        launch_id = str(draft["launch_id"])
        entry = by_id.setdefault(launch_id, {"launch_id": launch_id})
        entry.update(
            {
                "state": str(draft["status"]),
                "config": str(draft["path"]),
                "draft": True,
            }
        )
    return tuple(by_id[key] for key in sorted(by_id))


def execute(state: Any, record: Mapping[str, Any], action: str) -> Any:
    owner = _owner(state)
    launch_id = str(record["launch_id"])
    runtime = LaunchRuntimeApplication(owner)
    instance = record.get("instance_id")
    config_path = _config_path(owner, record)
    if action == "validate":
        return LaunchConfigurationApplication().validate(
            config_path, workspace_root=owner.paths.root
        )
    if action == "start":
        config = LaunchConfigurationApplication().load(
            config_path, workspace_root=owner.paths.root
        )
        return runtime.start(config)
    if action == "status":
        return runtime.status(launch_id, instance=str(instance) if instance else None)
    if action == "stop":
        return runtime.stop(
            launch_id,
            instance=str(instance) if instance else None,
            mode=str(record.get("mode")) if record.get("mode") else None,
        )
    if action == "report":
        return runtime.report(launch_id, instance=str(instance) if instance else None)
    if action == "instances":
        return LaunchRegistryApplication(owner).instances(launch_id)
    if action == "logs":
        return runtime.logs(launch_id, instance=str(instance) if instance else None)
    if action == "wait":
        return runtime.wait(launch_id, instance=str(instance) if instance else None)
    if action == "restart":
        return runtime.restart(
            launch_id,
            instance=str(instance) if instance else None,
            config_path=config_path,
        )
    if action == "config":
        if record.get("draft"):
            return {
                "draft": load_values(config_path),
                "readiness": LaunchConfigurationApplication().validate(
                    config_path, workspace_root=owner.paths.root
                ),
            }
        return LaunchConfigurationApplication().explain(
            config_path, workspace_root=owner.paths.root
        )
    if action == "timeline":
        if not instance:
            raise ValueError("请先选择一个运行实例")
        workspace = owner.instance(str(record.get("mode")), launch_id, str(instance))
        return LaunchInstanceTimelineApplication(workspace).list(limit=200)
    raise ValueError(f"launch action requires another guided step: {action}")


def load_instances(state: Any, launch_id: str) -> tuple[dict[str, Any], ...]:
    return tuple(
        dict(record)
        for record in LaunchRegistryApplication(_owner(state)).instances(launch_id)
        if record.get("instance_id") and record.get("mode")
    )


def instance_overview(state: Any, launch_id: str, instance_id: str) -> dict[str, Any]:
    return LaunchRuntimeApplication(_owner(state)).status(
        launch_id, instance=instance_id
    )


def load_components(
    state: Any, launch_id: str, instance_id: str, mode: str
) -> tuple[dict[str, Any], ...]:
    owner = _owner(state)
    workspace = owner.instance(mode, launch_id, instance_id)
    values = LaunchRuntimeApplication(owner).component_status(workspace)
    return tuple(
        {"component": str(component), **dict(value)}
        for component, value in values.items()
    )


def load_timeline(
    state: Any, launch_id: str, instance_id: str, mode: str
) -> tuple[dict[str, Any], ...]:
    owner = _owner(state)
    workspace = owner.instance(mode, launch_id, instance_id)
    return tuple(LaunchInstanceTimelineApplication(workspace).list(limit=200))


def export_timeline(
    state: Any,
    launch_id: str,
    instance_id: str,
    mode: str,
    destination: str,
) -> Any:
    owner = _owner(state)
    workspace = owner.instance(mode, launch_id, instance_id)
    return LaunchInstanceTimelineApplication(workspace).export(destination)


def attach_snapshot(state: Any, launch_id: str) -> dict[str, Any]:
    application = LaunchRuntimeApplication(_owner(state))
    active = application.running_instance(launch_id)
    if active is None:
        raise RuntimeError(f"Launch 未在运行：{launch_id}")
    instance_id = str(active["instance_id"])
    return {
        "instance": dict(active),
        "status": application.status(launch_id, instance=instance_id),
        "logs": application.logs(launch_id, instance=instance_id, lines=300),
    }


def send_python(state: Any, launch_id: str, source: str) -> dict[str, Any]:
    owner = _owner(state)
    active = LaunchRuntimeApplication(owner).running_instance(launch_id)
    if active is None:
        raise RuntimeError(f"Launch 未在运行：{launch_id}")
    instance_id = str(active["instance_id"])
    mode = str(active.get("mode") or "paper")
    socket_path = owner.instance(mode, launch_id, instance_id).socket("strategy")
    return asyncio.run(
        UnixRestClient(socket_path).request(
            "POST",
            "/v1/command",
            json.dumps(
                {
                    "request_id": f"workbench:{instance_id}:{uuid4().hex}",
                    "kind": "interactive.python",
                    "source": source,
                },
                separators=(",", ":"),
            ).encode("utf-8"),
        )
    )


def preview(record: Mapping[str, Any], action: str) -> dict[str, Any]:
    return {
        "status": "preview",
        "action": action,
        "launch_id": str(record["launch_id"]),
    }


__all__ = [
    "ATTACH_ACTIONS",
    "LAUNCH_ACTIONS",
    "TIMELINE_ACTIONS",
    "AttachAction",
    "InstanceAction",
    "LaunchAction",
    "LaunchWizardState",
    "TimelineAction",
    "attach_snapshot",
    "components_renderable",
    "execute",
    "export_timeline",
    "instance_overview",
    "instance_actions",
    "instances_renderable",
    "load_components",
    "load_instances",
    "load_launches",
    "load_timeline",
    "open_edit_launch_wizard",
    "open_new_launch_wizard",
    "preview",
    "records_renderable",
    "save_launch_wizard",
    "send_python",
]
