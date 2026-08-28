"""Workspace Market controls owned by the Market Workbench slice."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
import time
from typing import Any, cast

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

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
    ActionItem("session-subscriptions", "当前 Kairos I 订阅", "查看本会话拥有的订阅", "3"),
    ActionItem("subscriptions", "Market 全部订阅", "查看所有策略、操作员和系统订阅", "4"),
    ActionItem("subscribe", "添加订阅", "为当前 Kairos I 会话添加行情订阅", "s"),
    ActionItem("unsubscribe", "退出订阅", "退出当前 Kairos I 会话拥有的订阅", "u"),
    ActionItem("snapshot", "查看行情快照", "读取 Quote、K 线或 Greeks", "5"),
    ActionItem("freshness", "查看行情新鲜度", "读取指定 Market 数据年龄", "6"),
    ActionItem("start", "启动服务", "启动 Workspace Market 服务", "7"),
    ActionItem("stop", "停止服务", "停止 Workspace Market 服务", "8"),
    ActionItem("restart", "重启服务", "重启 Workspace Market 服务", "9"),
    ActionItem("logs", "查看日志", "读取最近进程日志", "l"),
    ActionItem("pause", "暂停行情回放", "暂停 replay 时钟", "p"),
    ActionItem("resume", "继续行情回放", "恢复 replay 时钟", "r"),
)


@dataclass(slots=True)
class WorkspaceMarketPromptState:
    action: str
    default_market: str = ""
    owner_id: str = ""
    values: dict[str, str] = field(default_factory=dict)

    @property
    def dangerous(self) -> bool:
        return self.action in {
            "start",
            "stop",
            "restart",
            "pause",
            "resume",
            "subscribe",
            "unsubscribe",
        }

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
        if name in {
            "kind",
            "market-id",
            "timeframe",
            "observations",
            "subscription-id",
        } and not value:
            raise ValueError(f"{name} 不能为空")
        if name == "kind" and value not in {"quote", "bar", "greeks"}:
            raise ValueError("快照类型必须是 quote、bar 或 greeks")
        if name == "observations" and not tuple(
            item.strip() for item in value.split(",") if item.strip()
        ):
            raise ValueError("至少需要一个 observation")
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
        if self.action == "subscribe":
            return (
                ("market-id", "Market ID", self.default_market),
                ("observations", "Observations（逗号分隔）", "quote"),
                ("provider", "Provider（留空自动选择）", ""),
            )
        if self.action == "unsubscribe":
            return (("subscription-id", "Subscription ID", ""),)
        return ()


def execute(state: Any, prompt: WorkspaceMarketPromptState) -> dict[str, Any]:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 Workspace")
    owner = state.owner
    action = prompt.action
    processes = ComponentProcessApplication(owner)
    if action == "status":
        process = processes.status("market")
        if not process.get("control_reachable"):
            return {"process": process, "health": None}
        client = cast(
            MarketSystemClient,
            processes.client("market", owner.paths.process_socket("market")),
        )
        return {"process": process, "health": client.health()}
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
    if action == "session-subscriptions":
        return client.subscriptions(owner_id=prompt.owner_id)
    if action == "subscriptions":
        return client.subscriptions()
    if action == "subscribe":
        return client.operator_subscribe(
            owner_id=prompt.owner_id,
            request_id=f"kairos-i-subscribe:{time.time_ns()}",
            market_id=prompt.values["market-id"],
            observations=tuple(
                value.strip()
                for value in prompt.values["observations"].split(",")
                if value.strip()
            ),
            provider=prompt.values["provider"] or None,
        )
    if action == "unsubscribe":
        return client.operator_unsubscribe(
            owner_id=prompt.owner_id,
            request_id=f"kairos-i-unsubscribe:{time.time_ns()}",
            subscription_id=prompt.values["subscription-id"],
        )
    if action == "pause":
        return client.pause_replay()
    return client.resume_replay()


def release_operator_owner(state: Any, owner_id: str) -> tuple[str, ...]:
    """Release session-owned Market subscriptions during normal Workbench exit."""

    if state.owner is None:
        return ()
    owner = state.owner
    processes = ComponentProcessApplication(owner)
    client = cast(
        MarketSystemClient,
        processes.client("market", owner.paths.process_socket("market")),
    )
    result = client.operator_release_owner(
        owner_id=owner_id,
        request_id=f"kairos-i-release:{time.time_ns()}",
    )
    return tuple(str(value) for value in result["released_subscription_ids"])


def status_renderable(result: Mapping[str, Any]) -> RenderableType:
    process = _mapping(result.get("process"))
    health = _mapping(result.get("health"))
    process_table = Table.grid(padding=(0, 2))
    process_table.add_column(style="dim", no_wrap=True)
    process_table.add_column()
    process_table.add_row("进程状态", _status_text(process.get("status")))
    process_table.add_row(
        "控制连接", "可用" if process.get("control_reachable") else "不可用"
    )
    process_table.add_row("PID", str(process.get("pid") or "—"))
    if process.get("probe_error"):
        process_table.add_row("探测错误", str(process["probe_error"]))

    if not health:
        return Panel(process_table, title="Market Runtime", border_style="yellow")

    owner_table = Table.grid(padding=(0, 2))
    owner_table.add_column(style="dim", no_wrap=True)
    owner_table.add_column()
    owner_table.add_row("Owner 状态", _status_text(health.get("status")))
    owner_table.add_row("Feed", _status_text(health.get("feed_status")))
    owner_table.add_row("Actor", str(health.get("actor_id") or "—"))
    owner_table.add_row("Event sequence", str(health.get("event_sequence") or 0))
    owner_table.add_row(
        "Current view",
        " · ".join(
            (
                f"input {health.get('current_view_input_update_count', 0)}",
                f"commit {health.get('current_view_commit_count', 0)}",
                f"encoded {health.get('current_view_encoded_update_count', 0)}",
                f"order-book {health.get('current_view_order_book_encode_count', 0)}",
            )
        ),
    )
    latency_nanos = health.get("last_current_view_commit_latency_nanos")
    owner_table.add_row(
        "最近提交耗时",
        "—" if latency_nanos is None else f"{int(latency_nanos) / 1_000_000:.3f} ms",
    )
    attempts = int(health.get("notification_attempt_count") or 0)
    failures = int(health.get("notification_failure_count") or 0)
    owner_table.add_row(
        "通知",
        f"attempts {attempts} · failures {failures}"
        + (f" · error-rate {failures / attempts:.1%}" if attempts else ""),
    )
    return Group(
        Panel(process_table, title="Market Runtime", border_style="cyan"),
        Panel(owner_table, title="Market Data Plane", border_style="cyan"),
    )


def routes_renderable(result: Mapping[str, Any]) -> RenderableType:
    routes = tuple(
        value for value in result.get("routes", ()) if isinstance(value, Mapping)
    )
    summary: dict[tuple[str, str], int] = {}
    for route in routes:
        provider = str(route.get("provider") or "—")
        state = str(route.get("state") or "unknown")
        summary[(provider, state)] = summary.get((provider, state), 0) + 1

    table = Table(show_header=True, header_style="bold")
    table.add_column("Provider")
    table.add_column("状态")
    table.add_column("Routes", justify="right")
    table.add_column("说明")
    if summary:
        for (provider, state), count in sorted(
            summary.items(), key=lambda item: (item[0][0], item[0][1])
        ):
            selected = sum(
                1
                for route in routes
                if str(route.get("provider") or "—") == provider
                and str(route.get("state") or "unknown") == state
                and route.get("selected")
            )
            observations = sorted(
                {
                    str(observation)
                    for route in routes
                    if str(route.get("provider") or "—") == provider
                    and str(route.get("state") or "unknown") == state
                    for observation in route.get("observation_kinds", ())
                }
            )
            detail = ", ".join(observations) or "—"
            if selected:
                detail += f" · selected {selected}"
            table.add_row(provider, state, str(count), detail)
    else:
        table.add_row("—", "无路由", "0", "—")
    return Panel(
        table, title=f"Market Provider Routes · {len(routes)}", border_style="cyan"
    )


def subscriptions_renderable(
    result: Mapping[str, Any], *, current_session: bool
) -> RenderableType:
    subscriptions = tuple(
        value
        for value in result.get("subscriptions", ())
        if isinstance(value, Mapping)
    )
    table = Table(show_header=True, header_style="bold")
    table.add_column("Subscription")
    table.add_column("Owner")
    table.add_column("Market")
    table.add_column("Observations")
    table.add_column("Provider")
    table.add_column("状态")
    table.add_column("Pending")
    for subscription in subscriptions:
        table.add_row(
            str(subscription.get("subscription_id") or "—"),
            str(subscription.get("owner_id") or "—"),
            ", ".join(str(value) for value in subscription.get("market_ids", ()))
            or "—",
            ", ".join(str(value) for value in subscription.get("observations", ()))
            or "—",
            ", ".join(
                str(value) for value in subscription.get("selected_providers", ())
            )
            or "—",
            str(subscription.get("state") or "unknown"),
            str(subscription.get("pending_reason") or "—"),
        )
    if not subscriptions:
        table.add_row("—", "—", "—", "—", "—", "无订阅", "—")
    return Panel(
        table,
        title="当前 Kairos I 订阅" if current_session else "Market 全部订阅",
        border_style="cyan",
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _status_text(value: object) -> Text:
    label = str(value or "unknown")
    normalized = label.lower()
    style = (
        "green"
        if normalized in {"ready", "running", "active"}
        else "red"
        if normalized in {"degraded", "failed", "not_running", "disconnected"}
        else "yellow"
    )
    return Text(label, style=style)


def preview(prompt: WorkspaceMarketPromptState) -> dict[str, Any]:
    return {"status": "preview", **prompt.summary()}


def equivalent_command(
    state: Any, prompt: WorkspaceMarketPromptState
) -> tuple[str, ...] | None:
    """Return a stable CLI equivalent for connected read operations."""

    if state.owner is None or prompt.action not in {"snapshot", "freshness"}:
        return None
    owner = state.owner
    arguments = [
        "connected",
        prompt.action,
        "--socket",
        str(owner.paths.process_socket("market")),
        "--view-root",
        str(owner.paths.child("snapshots", "market", "market-shared")),
    ]
    if prompt.action == "snapshot":
        arguments.extend(
            (
                prompt.values["kind"],
                "--market-id",
                prompt.values["market-id"],
                "--provider",
                prompt.values["provider"],
            )
        )
        timeframe = prompt.values.get("timeframe")
        if timeframe:
            arguments.extend(("--timeframe", timeframe))
    else:
        arguments.extend(
            (
                "--market-id",
                prompt.values["market-id"],
                "--provider",
                prompt.values["provider"],
            )
        )
    return tuple(
        MarketCliApplication(owner, binary="kairos-market-cli").command(arguments)
    )


__all__ = [
    "WORKSPACE_MARKET_ACTIONS",
    "WorkspaceMarketPromptState",
    "execute",
    "equivalent_command",
    "preview",
    "routes_renderable",
    "subscriptions_renderable",
    "status_renderable",
    "release_operator_owner",
]
