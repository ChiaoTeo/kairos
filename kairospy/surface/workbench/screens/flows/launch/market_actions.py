"""Market actions owned by the Launch Workbench product slice."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from kairospy.system.apps.components.application import NativeCliApplication
from kairospy.system.apps.launch.application import LaunchRuntimeApplication
from kairospy.system.apps.launch.application.connections import (
    resolve_instance_connections,
)

from ....widgets import ActionItem


MARKET_COMPONENT_ACTIONS = (
    ActionItem("status", "组件状态", "读取当前实例绑定的 Market 状态", "1"),
    ActionItem("routes", "数据路由", "查看当前实例可用 provider route", "2"),
    ActionItem("quote", "报价快照", "按 Market ID 读取当前报价", "3"),
    ActionItem("bar", "K 线快照", "按 Market ID 和周期读取 K 线", "4"),
    ActionItem("greeks", "Greeks 快照", "读取期权 Greeks", "5"),
    ActionItem("freshness", "行情新鲜度", "查看 observation 更新时间", "6"),
    ActionItem("pause-replay", "暂停回放", "暂停实例 Market replay", "p"),
    ActionItem("resume-replay", "继续回放", "恢复实例 Market replay", "r"),
)


@dataclass(slots=True)
class LaunchMarketPromptState:
    action: str
    launch: Mapping[str, Any]
    default_market: str = ""
    values: dict[str, str] = field(default_factory=dict)

    @property
    def dangerous(self) -> bool:
        return self.action in {"pause-replay", "resume-replay"}

    def next_prompt(self) -> tuple[str, str, str] | None:
        for name, label, default in self._steps():
            if name not in self.values:
                return (
                    name,
                    label,
                    f"直接回车使用 {default}；输入 /back 取消。"
                    if default
                    else "可直接回车留空；输入 /back 取消。",
                )
        return None

    def accept(self, name: str, raw: str) -> None:
        default = next(
            default for field, _label, default in self._steps() if field == name
        )
        value = raw.strip() or default
        if name in {"market-id", "timeframe"} and not value:
            raise ValueError(f"{name} 不能为空")
        self.values[name] = value

    def summary(self) -> dict[str, Any]:
        return {
            "scope": "launch-instance",
            "launch_id": self.launch.get("launch_id"),
            "instance_id": self.launch.get("instance_id"),
            "mode": self.launch.get("mode"),
            "action": self.action,
            **self.values,
        }

    def _steps(self) -> tuple[tuple[str, str, str], ...]:
        if self.action in {"quote", "greeks"}:
            return (
                ("market-id", "Market ID", self.default_market),
                ("provider", "Provider（可留空使用当前 route）", ""),
            )
        if self.action == "bar":
            return (
                ("market-id", "Market ID", self.default_market),
                ("provider", "Provider（可留空使用当前 route）", ""),
                ("timeframe", "K 线周期", "1m"),
            )
        if self.action == "freshness":
            return (
                ("market-id", "Market ID", self.default_market),
                ("provider", "Provider（可留空使用当前 route）", ""),
                ("observation", "Observation（可留空）", ""),
            )
        return ()


def execute(state: Any, prompt: LaunchMarketPromptState) -> dict[str, Any]:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 Workspace")
    launch_id = str(prompt.launch.get("launch_id") or "")
    instance_id = str(prompt.launch.get("instance_id") or "")
    mode = str(prompt.launch.get("mode") or "")
    if not launch_id or not instance_id or not mode:
        raise ValueError("必须先选择具体 Launch instance")
    owner = state.owner
    instance = owner.instance(mode, launch_id, instance_id)
    if prompt.action == "status":
        value = LaunchRuntimeApplication(owner).component_status(instance)["market"]
    else:
        connection = resolve_instance_connections(instance).market
        if connection is None:
            raise RuntimeError("当前实例没有绑定 Market 组件")
        if not connection.socket.exists():
            raise RuntimeError("当前实例的 Market socket 不可用")
        target = ["--socket", str(connection.socket)]
        if connection.view_root is not None:
            target.extend(("--view-root", str(connection.view_root)))
        command = prompt.action
        arguments: list[str] = []
        values = prompt.values
        if prompt.action in {"quote", "bar", "greeks"}:
            command = "snapshot"
            arguments.extend((prompt.action, "--market-id", values["market-id"]))
            if values.get("provider"):
                arguments.extend(("--provider", values["provider"]))
            if prompt.action == "bar":
                arguments.extend(("--timeframe", values["timeframe"]))
        elif prompt.action == "freshness":
            arguments.extend(("--market-id", values["market-id"]))
            if values.get("provider"):
                arguments.extend(("--provider", values["provider"]))
            if values.get("observation"):
                arguments.extend(("--observation", values["observation"]))
        value = NativeCliApplication(owner).run(
            "market", ["connected", command, *target, *arguments]
        )
    return {
        **value,
        "launch_id": launch_id,
        "instance_id": instance_id,
        "mode": mode,
        "scope": "launch-instance",
    }


def preview(prompt: LaunchMarketPromptState) -> dict[str, Any]:
    return {"status": "preview", **prompt.summary()}


__all__ = [
    "MARKET_COMPONENT_ACTIONS",
    "LaunchMarketPromptState",
    "execute",
    "preview",
]
