"""Launch discovery and control actions for the Workbench product slice."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
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


LAUNCH_ACTIONS = (
    ActionItem("validate", "校验配置", "检查配置结构和所需资源", "1"),
    ActionItem("status", "查看运行状态", "读取策略与依赖组件状态", "2"),
    ActionItem("start", "启动", "按当前配置启动新的运行实例", "3"),
    ActionItem("stop", "停止", "停止策略并释放运行资源", "4"),
    ActionItem("report", "查看回测报告", "读取最近完成的回测结果", "5"),
    ActionItem("instances", "查看运行实例", "列出当前及历史实例", "6"),
    ActionItem("logs", "查看日志", "读取最近的策略进程日志", "7"),
    ActionItem("wait", "等待回测完成", "等待并读取回测报告", "8"),
    ActionItem("restart", "重启", "停止当前实例并启动新实例", "9"),
    ActionItem("edit", "编辑配置", "逐字段修改 Launch 配置", "e"),
    ActionItem("config", "查看配置", "解释规范化配置和运行计划", "0"),
    ActionItem("attach", "跟随运行输出", "持续刷新状态和策略日志", "a"),
    ActionItem("timeline", "查看实例时间线", "读取生命周期审计记录", "t"),
)

INSTANCE_ACTIONS = (
    ActionItem("overview", "实例概览", "读取运行状态和注册信息", "1"),
    ActionItem("components", "实例组件", "查看 Market、Execution、Risk 等组件", "2"),
    ActionItem("timeline", "实例时间线", "查看生命周期审计记录", "3"),
)

ATTACH_ACTIONS = (
    ActionItem("refresh", "刷新运行输出", "读取当前状态与最近日志", "1"),
    ActionItem("pause", "暂停或继续", "控制后台运行输出刷新", "p"),
    ActionItem("clear", "清空当前窗口", "只清除当前可见日志，不删除日志源", "c"),
    ActionItem("python", "发送 Strategy Python", "向当前 Strategy 提交一行代码", "2"),
)

TIMELINE_ACTIONS = (
    ActionItem("refresh", "刷新时间线", "读取最近 200 条生命周期记录", "1"),
    ActionItem("export", "导出 JSONL", "写入指定的时间线导出文件", "2"),
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
    "INSTANCE_ACTIONS",
    "LAUNCH_ACTIONS",
    "TIMELINE_ACTIONS",
    "LaunchWizardState",
    "attach_snapshot",
    "components_renderable",
    "execute",
    "export_timeline",
    "instance_overview",
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
