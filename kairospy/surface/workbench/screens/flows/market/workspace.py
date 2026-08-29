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


LIVE_MARKET_ACTIONS = (
    ActionItem(
        "session-subscriptions", "当前关注", "查看本次 Kairos I 会话正在接收的行情", "1"
    ),
    ActionItem("subscribe", "添加实时行情", "选择市场并接收实时报价", "s"),
    ActionItem(
        "subscribe-custom",
        "自定义行情内容",
        "选择市场以及要接收的行情内容",
        "x",
    ),
    ActionItem("unsubscribe", "停止关注", "停止接收当前会话选择的行情", "u"),
    ActionItem("snapshot", "查看行情快照", "读取实时报价、K 线或 Greeks", "2"),
    ActionItem("freshness", "查看行情新鲜度", "查看所选行情的更新时间", "3"),
)

LIVE_MARKET_UNAVAILABLE_ACTIONS = (
    ActionItem(
        "prepare",
        "启动实时行情",
        "启动并保持项目共享行情服务运行",
        "1",
    ),
    ActionItem(
        "service-details",
        "查看服务详细状态",
        "前往运行中心查看状态、日志和技术诊断",
        "2",
    ),
    ActionItem("history", "改看历史行情", "返回行情入口并使用独立历史数据", "3"),
    ActionItem("back", "返回", "返回市场与行情入口", "4"),
)


def live_market_available(state: Any) -> bool:
    """Project the latest known shared Market availability without probing in UI."""

    snapshot = getattr(state, "snapshot", None)
    services = getattr(snapshot, "shared_services", None)
    if not isinstance(services, Mapping):
        return True
    market = services.get("market")
    if not isinstance(market, Mapping):
        return True
    status = str(market.get("status") or "unknown").lower()
    if status in {"not_running", "stopped", "failed", "start_failed", "error"}:
        return False
    return market.get("control_reachable") is not False


def unavailable_renderable() -> RenderableType:
    return Group(
        conclusion("实时行情暂不可用", tone=ResultTone.WARNING),
        facts(
            (
                ("原因", "当前项目的共享行情服务尚未就绪"),
                ("影响", "暂时不能查看或管理实时行情；历史行情仍可使用"),
                ("作用域", "项目共享行情服务"),
            )
        ),
    )


SUBSCRIPTION_CONTENT_ACTIONS = (
    ActionItem("quote", "实时报价", "买卖报价与最新价格", "1"),
    ActionItem("quote,trade", "实时报价与逐笔成交", "同时接收报价和成交明细", "2"),
    ActionItem("bar:1m", "1 分钟 K 线", "接收一分钟聚合行情", "3"),
    ActionItem("greeks", "期权 Greeks", "接收期权风险指标", "4"),
)

SNAPSHOT_KIND_ACTIONS = (
    ActionItem("quote", "实时报价", "查看最新买卖报价", "1"),
    ActionItem("bar", "K 线", "查看指定周期的最新 K 线", "2"),
    ActionItem("greeks", "期权 Greeks", "查看最新期权风险指标", "3"),
)

TIMEFRAME_ACTIONS = (
    ActionItem("1m", "1 分钟", "一分钟 K 线", "1"),
    ActionItem("5m", "5 分钟", "五分钟 K 线", "2"),
    ActionItem("15m", "15 分钟", "十五分钟 K 线", "3"),
    ActionItem("1h", "1 小时", "一小时 K 线", "4"),
    ActionItem("1d", "1 天", "日 K 线", "5"),
)


_ACTION_LABELS = {
    "snapshot": "查看行情快照",
    "freshness": "查看行情新鲜度",
    "subscribe": "添加实时行情",
    "unsubscribe": "退出行情",
}

_OBSERVATION_LABELS = {
    "quote": "实时报价",
    "trade": "逐笔成交",
    "bar:1m": "1 分钟 K 线",
    "greeks": "期权 Greeks",
}

_SNAPSHOT_LABELS = {
    "quote": "实时报价",
    "bar": "K 线",
    "greeks": "期权 Greeks",
}


@dataclass(slots=True)
class WorkspaceMarketPromptState:
    action: str
    default_market: str = ""
    owner_id: str = ""
    custom_content: bool = False
    market_label: str = ""
    market_description: str = ""
    subscription_label: str = ""
    subscription_description: str = ""
    provider_resolved: bool = False
    values: dict[str, str] = field(default_factory=dict)

    @property
    def dangerous(self) -> bool:
        return False

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
            (default for field, _label, default in self._steps() if field == name),
            "",
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

    def select_market(
        self, market_id: str, *, label: str, description: str = ""
    ) -> None:
        """Retain the canonical identity while presenting human market vocabulary."""

        if not market_id:
            raise ValueError("所选市场没有有效的市场标识")
        self.values["market-id"] = market_id
        self.values.setdefault("provider", "")
        self.market_label = label
        self.market_description = description

    def select_subscription(
        self, subscription_id: str, *, label: str, description: str = ""
    ) -> None:
        """Retain an opaque subscription identity behind its business label."""

        if not subscription_id:
            raise ValueError("所选行情没有有效的订阅标识")
        self.values["subscription-id"] = subscription_id
        self.subscription_label = label
        self.subscription_description = description

    def summary(self) -> dict[str, Any]:
        return {"scope": "workspace", "action": self.action, **self.values}

    def _steps(self) -> tuple[tuple[str, str, str], ...]:
        if self.action == "snapshot":
            kind = self.values.get("kind", "")
            values = [
                ("market-id", "市场", self.default_market),
                ("kind", "行情内容", "quote"),
            ]
            if kind == "bar":
                values.append(("timeframe", "K 线周期", "1m"))
            return tuple(values)
        if self.action == "freshness":
            return (("market-id", "市场", self.default_market),)
        if self.action == "subscribe":
            values = [("market-id", "市场", self.default_market)]
            if self.custom_content:
                values.append(("observations", "行情内容", "quote"))
            return tuple(values)
        if self.action == "unsubscribe":
            return (("subscription-id", "当前会话行情", ""),)
        return ()


def prompt_renderable(prompt: WorkspaceMarketPromptState) -> RenderableType:
    """Present one Workspace Market operation without exposing request JSON."""

    table = Table.grid(padding=(0, 3))
    table.add_column(style="dim", no_wrap=True)
    table.add_column()
    table.add_row("操作", _ACTION_LABELS.get(prompt.action, "Market 操作"))
    if "market-id" in prompt.values:
        market = prompt.market_label or "已选市场"
        if prompt.market_description:
            market = f"{market} · {prompt.market_description}"
        table.add_row("市场", market)
    if "kind" in prompt.values:
        table.add_row(
            "行情内容",
            _SNAPSHOT_LABELS.get(prompt.values["kind"], prompt.values["kind"]),
        )
    if "timeframe" in prompt.values:
        table.add_row("K 线周期", prompt.values["timeframe"])
    if prompt.action == "subscribe":
        observations = prompt.values.get("observations", "quote")
        labels = [
            _OBSERVATION_LABELS.get(value, value)
            for value in observations.split(",")
            if value
        ]
        table.add_row("行情内容", "、".join(labels) or "实时报价")
        table.add_row("数据来源", "由 Market 自动选择")
        table.add_row("订阅归属", "当前 Kairos I 会话")
    if "subscription-id" in prompt.values:
        subscription = prompt.subscription_label or "已选行情"
        if prompt.subscription_description:
            subscription = f"{subscription} · {prompt.subscription_description}"
        table.add_row("退出行情", subscription)
    return table


def execute(state: Any, prompt: WorkspaceMarketPromptState) -> dict[str, Any]:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 Workspace")
    owner = state.owner
    action = prompt.action
    processes = ComponentProcessApplication(owner)
    market = MarketCliApplication(owner)
    if action == "snapshot":
        return market.connected_snapshot(
            market_id=prompt.values["market-id"],
            provider=prompt.values.get("provider", ""),
            kind=prompt.values["kind"],
            timeframe=prompt.values.get("timeframe"),
        )
    if action == "freshness":
        return market.connected_freshness(
            market_id=prompt.values["market-id"],
            provider=prompt.values.get("provider", ""),
        )
    client = cast(
        MarketSystemClient,
        processes.client("market", owner.paths.process_socket("market")),
    )
    if action == "session-subscriptions":
        return client.subscriptions(owner_id=prompt.owner_id)
    if action == "subscribe":
        observations = prompt.values.get("observations", "quote")
        return client.operator_subscribe(
            owner_id=prompt.owner_id,
            request_id=f"kairos-i-subscribe:{time.time_ns()}",
            market_id=prompt.values["market-id"],
            observations=tuple(
                value.strip() for value in observations.split(",") if value.strip()
            ),
            provider=prompt.values.get("provider") or None,
        )
    if action == "unsubscribe":
        return client.operator_unsubscribe(
            owner_id=prompt.owner_id,
            request_id=f"kairos-i-unsubscribe:{time.time_ns()}",
            subscription_id=prompt.values["subscription-id"],
        )
    raise ValueError(f"unknown live Market action: {action}")


def provider_options(
    state: Any, prompt: WorkspaceMarketPromptState
) -> tuple[Mapping[str, Any], ...]:
    """Read eligible providers only when a connected view needs one explicitly."""

    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 Workspace")
    owner = state.owner
    processes = ComponentProcessApplication(owner)
    client = cast(
        MarketSystemClient,
        processes.client("market", owner.paths.process_socket("market")),
    )
    observation = prompt.values.get("kind", "quote")
    result = client.data_routes(
        market_id=prompt.values["market-id"],
        observation_kind=observation,
        configured_only=True,
        ready_only=True,
    )
    return tuple(
        value for value in result.get("routes", ()) if isinstance(value, Mapping)
    )


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
    table.add_column("数据来源")
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
    if not current_session:
        table.add_column("归属")
    table.add_column("市场")
    table.add_column("行情内容")
    table.add_column("数据来源")
    table.add_column("状态")
    table.add_column("说明")
    for subscription in subscriptions[:20]:
        row = []
        if not current_session:
            row.append(_owner_display(subscription.get("owner_id")))
        row.extend(
            (
                "、".join(
                    _market_display(str(value))
                    for value in subscription.get("market_ids", ())
                )
                or "—",
                "、".join(
                    _OBSERVATION_LABELS.get(str(value), str(value))
                    for value in subscription.get("observations", ())
                )
                or "—",
                "、".join(
                    str(value) for value in subscription.get("selected_providers", ())
                )
                or "自动选择",
                str(subscription.get("state") or "unknown"),
                str(subscription.get("pending_reason") or "—"),
            )
        )
        table.add_row(*row)
    if not subscriptions:
        empty = ["—"] if not current_session else []
        table.add_row(*empty, "—", "—", "—", "无订阅", "—")
    visible = min(len(subscriptions), 20)
    title = "当前关注" if current_session else "全部运行订阅"
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


def mutation_renderable(
    result: Mapping[str, Any], prompt: WorkspaceMarketPromptState
) -> RenderableType:
    """Render a subscription mutation as an outcome, not a protocol response."""

    if prompt.action == "unsubscribe":
        return Group(
            conclusion(
                f"已退出 {prompt.subscription_label or '所选行情'}",
                tone=ResultTone.SUCCESS,
            ),
            prompt_renderable(prompt),
        )
    state = str(result.get("state") or "unknown")
    pending = result.get("pending_reason")
    providers = tuple(str(value) for value in result.get("resolved_providers", ()))
    rows: list[tuple[str, RenderableType]] = [
        ("市场", prompt.market_label or "已选市场"),
        (
            "行情内容",
            "、".join(
                _OBSERVATION_LABELS.get(value, value)
                for value in prompt.values.get("observations", "quote").split(",")
                if value
            ),
        ),
        ("数据来源", "、".join(providers) or "由 Market 自动选择"),
        ("状态", _status_text(state)),
    ]
    if pending:
        rows.append(("说明", str(pending)))
    return Group(
        conclusion(
            (
                f"{prompt.market_label or '所选市场'} 行情已开始接收"
                if state == "active"
                else f"{prompt.market_label or '所选市场'} 行情请求已提交"
            ),
            tone=ResultTone.SUCCESS if state == "active" else ResultTone.WARNING,
        ),
        facts(rows),
    )


def _market_display(market_id: str) -> str:
    parts = tuple(part for part in market_id.split(":") if part)
    return parts[-1] if parts else "未知市场"


def _owner_display(value: object) -> str:
    owner = str(value or "—")
    if owner.startswith("operator:kairos-i:"):
        return "Kairos I 会话"
    if owner.startswith("strategy:"):
        return owner.removeprefix("strategy:")
    return owner


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
                prompt.values.get("provider", ""),
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
                prompt.values.get("provider", ""),
            )
        )
    arguments.extend(("--workspace", str(owner.paths.root), "--format", "json"))
    return tuple(arguments)


__all__ = [
    "SNAPSHOT_KIND_ACTIONS",
    "SUBSCRIPTION_CONTENT_ACTIONS",
    "TIMEFRAME_ACTIONS",
    "LIVE_MARKET_ACTIONS",
    "LIVE_MARKET_UNAVAILABLE_ACTIONS",
    "WorkspaceMarketPromptState",
    "execute",
    "equivalent_command",
    "preview",
    "provider_options",
    "prompt_renderable",
    "mutation_renderable",
    "live_market_available",
    "routes_renderable",
    "subscriptions_renderable",
    "status_renderable",
    "unavailable_renderable",
    "release_operator_owner",
]
