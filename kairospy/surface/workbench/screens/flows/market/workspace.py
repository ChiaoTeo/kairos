"""Workspace Market controls owned by the Market Workbench slice."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from kairospy.investment.apps.market.application.cli import MarketCliApplication
from kairospy.system.apps.components.application import ComponentProcessApplication
from kairospy.system.apps.components.application.clients import MarketSystemClient
from kairospy.system.apps.launch.application import (
    WorkspaceComponentDependencyApplication,
)

from ....widgets import ActionItem


WORKSPACE_MARKET_ACTIONS = (
    ActionItem("status", "查看状态", "读取服务健康与运行资源", "1"),
    ActionItem("routes", "查看数据路由", "读取已配置 Provider route", "2"),
    ActionItem("snapshot", "查看行情快照", "读取 Quote、K 线或 Greeks", "3"),
    ActionItem("freshness", "查看行情新鲜度", "读取指定 Market 数据年龄", "4"),
    ActionItem("start", "启动服务", "启动 Workspace Market 服务", "5"),
    ActionItem("stop", "停止服务", "停止 Workspace Market 服务", "6"),
    ActionItem("restart", "重启服务", "重启 Workspace Market 服务", "7"),
    ActionItem("logs", "查看日志", "读取最近进程日志", "8"),
    ActionItem("pause", "暂停行情回放", "暂停 replay 时钟", "p"),
    ActionItem("resume", "继续行情回放", "恢复 replay 时钟", "r"),
)


@dataclass(slots=True)
class WorkspaceMarketPromptState:
    action: str
    default_market: str = ""
    values: dict[str, str] = field(default_factory=dict)

    @property
    def dangerous(self) -> bool:
        return self.action in {"start", "stop", "restart", "pause", "resume"}

    def next_prompt(self) -> tuple[str, str, str] | None:
        for name, label, default in self._steps():
            if name not in self.values:
                detail = (
                    f"直接回车使用 {default}；输入 /back 取消。"
                    if default
                    else "可直接回车留空；输入 /back 取消。"
                )
                return name, label, detail
        return None

    def accept(self, name: str, raw: str) -> None:
        default = next(
            default for field, _label, default in self._steps() if field == name
        )
        value = raw.strip() or default
        if name in {"kind", "market-id", "timeframe"} and not value:
            raise ValueError(f"{name} 不能为空")
        if name == "kind" and value not in {"quote", "bar", "greeks"}:
            raise ValueError("快照类型必须是 quote、bar 或 greeks")
        self.values[name] = value

    def summary(self) -> dict[str, Any]:
        return {"scope": "workspace", "action": self.action, **self.values}

    def _steps(self) -> tuple[tuple[str, str, str], ...]:
        if self.action == "snapshot":
            kind = self.values.get("kind", "")
            values = [
                ("kind", "快照类型（quote / bar / greeks）", "quote"),
                ("market-id", "Market ID", self.default_market),
                ("provider", "Provider", ""),
            ]
            if kind == "bar":
                values.append(("timeframe", "K 线周期", "1m"))
            return tuple(values)
        if self.action == "freshness":
            return (
                ("market-id", "Market ID", self.default_market),
                ("provider", "Provider", ""),
            )
        return ()


def execute(state: Any, prompt: WorkspaceMarketPromptState) -> dict[str, Any]:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 Workspace")
    owner = state.owner
    action = prompt.action
    processes = ComponentProcessApplication(owner)
    if action == "status":
        return processes.status("market")
    if action == "start":
        return processes.ensure_running("market").status()
    if action in {"stop", "restart"}:
        WorkspaceComponentDependencyApplication(owner).require_clear("market", action)
        return (
            processes.stop("market")
            if action == "stop"
            else processes.restart("market").status()
        )
    if action == "logs":
        return {"component": "market", "lines": list(processes.logs("market"))}
    market = MarketCliApplication(owner)
    if action == "snapshot":
        return market.connected_snapshot(
            market_id=prompt.values["market-id"],
            provider=prompt.values["provider"],
            kind=prompt.values["kind"],
            timeframe=prompt.values.get("timeframe"),
        )
    if action == "freshness":
        return market.connected_freshness(
            market_id=prompt.values["market-id"],
            provider=prompt.values["provider"],
        )
    client = cast(
        MarketSystemClient,
        processes.client("market", owner.paths.process_socket("market")),
    )
    if action == "routes":
        return client.data_routes()
    if action == "pause":
        return client.pause_replay()
    return client.resume_replay()


def preview(prompt: WorkspaceMarketPromptState) -> dict[str, Any]:
    return {"status": "preview", **prompt.summary()}


__all__ = [
    "WORKSPACE_MARKET_ACTIONS",
    "WorkspaceMarketPromptState",
    "execute",
    "preview",
]
