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
from ...presentation import (
    ResultTone,
    conclusion,
    count,
    duration_from_nanos,
    facts,
    percentage,
    section,
)

from ....widgets import ActionItem


WORKSPACE_MARKET_ACTIONS = (
    ActionItem("status", "查看状态", "读取服务健康与运行资源", "1"),
    ActionItem("routes", "查看数据路由", "读取已配置 Provider route", "2"),
    ActionItem(
        "session-subscriptions", "当前 Kairos I 订阅", "查看本会话拥有的订阅", "3"
    ),
    ActionItem(
        "subscriptions", "Market 全部订阅", "查看所有策略、操作员和系统订阅", "4"
    ),
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
        if (
            name
            in {
                "kind",
                "market-id",
                "timeframe",
                "observations",
                "subscription-id",
            }
            and not value
        ):
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
    process_rows: list[tuple[str, RenderableType]] = [
        ("进程", _status_text(process.get("status"))),
        ("控制连接", "可用" if process.get("control_reachable") else "不可用"),
        ("PID", str(process.get("pid") or "—")),
    ]
    if process.get("probe_error"):
        process_rows.append(("探测错误", str(process["probe_error"])))

    if not health:
        return Group(
            conclusion(
                "Market 进程可见，但数据面健康状态不可用", tone=ResultTone.WARNING
            ),
            facts(process_rows),
        )

    ready = (
        str(health.get("status") or "").lower() == "ready"
        and str(health.get("feed_status") or "").lower() == "ready"
        and bool(process.get("control_reachable"))
    )
    attempts = int(health.get("notification_attempt_count") or 0)
    failures = int(health.get("notification_failure_count") or 0)
    latency_nanos = health.get("last_current_view_commit_latency_nanos")
    owner_rows: list[tuple[str, RenderableType]] = [
        ("数据面", _status_text(health.get("status"))),
        ("Feed", _status_text(health.get("feed_status"))),
        ("Actor", str(health.get("actor_id") or "—")),
        ("事件序号", count(int(health.get("event_sequence") or 0))),
        (
            "当前视图",
            " · ".join(
                (
                    f"输入 {count(int(health.get('current_view_input_update_count') or 0))}",
                    f"提交 {count(int(health.get('current_view_commit_count') or 0))}",
                    f"编码 {count(int(health.get('current_view_encoded_update_count') or 0))}",
                    f"订单簿 {count(int(health.get('current_view_order_book_encode_count') or 0))}",
                )
            ),
        ),
        (
            "最近提交耗时",
            "—" if latency_nanos is None else duration_from_nanos(int(latency_nanos)),
        ),
        (
            "通知",
            f"尝试 {count(attempts)} · 失败 {count(failures)} · "
            f"错误率 {percentage(failures, attempts)}",
        ),
    ]
    return Group(
        conclusion(
            "Market 服务与数据面均已就绪"
            if ready
            else "Market 可访问，但存在需要处理的运行状态",
            tone=ResultTone.SUCCESS if ready else ResultTone.WARNING,
        ),
        facts((*process_rows, *owner_rows)),
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
    table.add_column("路由数", justify="right")
    table.add_column("说明")
    summary_rows = sorted(summary.items(), key=lambda item: (item[0][0], item[0][1]))
    if summary_rows:
        for (provider, state), route_count in summary_rows[:20]:
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
                detail += f" · 已选 {count(selected)}"
            table.add_row(provider, _status_text(state), count(route_count), detail)
    else:
        table.add_row("—", "无路由", "0", "—")
    visible = min(len(summary_rows), 20)
    return Group(
        conclusion(
            (
                f"已配置 {count(len(routes))} 条数据路由，覆盖 "
                f"{count(len({provider for provider, _state in summary}))} 个 Provider"
                if routes
                else "当前没有已配置的数据路由"
            ),
            tone=ResultTone.SUCCESS if routes else ResultTone.WARNING,
        ),
        section("Provider 路由", table),
        Text(
            f"显示 {count(visible)} 组 · 其余 {count(len(summary_rows) - visible)} 组"
            if len(summary_rows) > visible
            else f"共 {count(len(summary_rows))} 组 Provider 状态",
            style="dim",
        ),
    )


def subscriptions_renderable(
    result: Mapping[str, Any], *, current_session: bool
) -> RenderableType:
    subscriptions = tuple(
        value for value in result.get("subscriptions", ()) if isinstance(value, Mapping)
    )
    table = Table(show_header=True, header_style="bold")
    table.add_column("Subscription")
    table.add_column("Owner")
    table.add_column("Market")
    table.add_column("Observations")
    table.add_column("Provider")
    table.add_column("状态")
    table.add_column("Pending")
    for subscription in subscriptions[:20]:
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
    visible = min(len(subscriptions), 20)
    title = "当前 Kairos I 订阅" if current_session else "Market 全部订阅"
    return Group(
        conclusion(
            f"{title}共 {count(len(subscriptions))} 条",
            tone=ResultTone.SUCCESS if subscriptions else ResultTone.WARNING,
        ),
        section(title, table),
        Text(
            f"显示 {count(visible)} 条 · 其余 {count(len(subscriptions) - visible)} 条"
            if len(subscriptions) > visible
            else f"共 {count(len(subscriptions))} 条",
            style="dim",
        ),
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
    """Return the public Workspace CLI equivalent for connected reads."""

    if state.owner is None or prompt.action not in {"snapshot", "freshness"}:
        return None
    owner = state.owner
    arguments = [
        "kairos",
        "system",
        "component",
        "market",
        prompt.action,
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
    arguments.extend(("--workspace", str(owner.paths.root), "--format", "json"))
    return tuple(arguments)


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
