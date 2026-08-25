"""Market discovery and observation actions for the Workbench slice."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table
from rich.text import Text

from kairospy.investment.apps.market.application.cli import MarketCliApplication

from ....widgets import ActionItem


@dataclass(slots=True)
class MarketFilePromptState:
    """Explicit single-input state for Market download and replay actions."""

    action: str
    market: Any
    values: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action == "download":
            symbol = self.market.venue_symbol or self.market.instrument.display_symbol
            provider = (
                "binance" if str(self.market.instrument_kind) == "spot" else "massive"
            )
            self.values.update(
                provider=provider,
                data_kind="bar",
                start=(
                    datetime.now(timezone.utc).date() - timedelta(days=30)
                ).isoformat(),
                end=datetime.now(timezone.utc).date().isoformat(),
                destination=f"market-history/{_safe_filename(str(symbol))}-bar.jsonl",
            )

    @property
    def dangerous(self) -> bool:
        return True

    def next_prompt(self) -> tuple[str, str, str] | None:
        if self.action == "replay":
            if "files" not in self.values:
                return (
                    "files",
                    "行情事件文件（多个路径用逗号分隔）",
                    "文件必须是 JSONL；输入 /back 取消。",
                )
            return None
        fields = (
            ("data_kind", "历史行情类型（bar / quote / trade）"),
            ("start", "开始日期（YYYY-MM-DD）"),
            ("end", "结束日期（YYYY-MM-DD）"),
            ("destination", "保存位置"),
        )
        completed = set(self.values.get("accepted", ()))
        for name, label in fields:
            if name not in completed:
                default = self.values[name]
                return name, label, f"直接回车使用 {default}；输入 /back 取消。"
        return None

    def accept(self, name: str, value: str) -> None:
        if self.action == "replay":
            files = tuple(part.strip() for part in value.split(",") if part.strip())
            if not files:
                raise ValueError("至少输入一个 JSONL 行情事件文件。")
            self.values["files"] = files
            return
        actual = value.strip() or str(self.values[name])
        if name == "data_kind":
            allowed = {"bar", "quote", "trade"}
            if self.values["provider"] == "massive":
                allowed.remove("trade")
            if actual not in allowed:
                raise ValueError(f"行情类型必须是：{' / '.join(sorted(allowed))}")
            self.values[name] = actual
            symbol = self.market.venue_symbol or self.market.instrument.display_symbol
            self.values["destination"] = (
                f"market-history/{_safe_filename(str(symbol))}-{actual}.jsonl"
            )
        elif name in {"start", "end"}:
            try:
                date.fromisoformat(actual)
            except ValueError as error:
                raise ValueError("日期必须使用 YYYY-MM-DD 格式。") from error
            self.values[name] = actual
        elif not actual:
            raise ValueError("保存位置不能为空。")
        else:
            self.values[name] = actual
        accepted: set[str] = set(self.values.get("accepted", ()))
        accepted.add(name)
        self.values["accepted"] = tuple(accepted)

    def summary(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "market_id": str(self.market.id),
            **{key: value for key, value in self.values.items() if key != "accepted"},
        }


@dataclass(frozen=True, slots=True)
class MarketRouteView:
    """Typed provider route retained by the Workbench presentation session."""

    provider: str
    market_type: str | None = None
    observation_kind: str | None = None
    environment: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MarketRouteView":
        def optional(name: str) -> str | None:
            raw = value.get(name)
            return str(raw) if raw is not None and raw != "" else None

        return cls(
            provider=str(value.get("provider") or ""),
            market_type=optional("market_type"),
            observation_kind=optional("observation_kind"),
            environment=optional("environment"),
        )

    @property
    def description(self) -> str:
        values = tuple(
            value
            for value in (self.market_type, self.observation_kind, self.environment)
            if value
        )
        return " · ".join(values) or "读取该 Provider 的行情"


def execute_file_action(state: Any, prompt: MarketFilePromptState) -> dict[str, Any]:
    application = MarketCliApplication(state.owner)
    market = prompt.market
    symbol = market.venue_symbol or market.instrument.display_symbol
    if prompt.action == "replay":
        return application.replay(
            market_id=str(market.id),
            instrument_id=str(market.instrument.id),
            exchange_id=str(market.exchange_id).rsplit(":", 1)[-1],
            market_type=str(market.instrument_kind),
            symbol=str(symbol),
            files=tuple(prompt.values["files"]),
        )
    return application.download(
        provider=str(prompt.values["provider"]),
        symbol=str(symbol),
        market_type=str(market.instrument_kind),
        data_kind=str(prompt.values["data_kind"]),
        instrument_id=str(market.instrument.id),
        start_unix_millis=_history_time_millis(
            str(prompt.values["start"]), end_of_day=False
        ),
        end_unix_millis=_history_time_millis(
            str(prompt.values["end"]), end_of_day=True
        ),
        destination=str(prompt.values["destination"]),
        market_id=(
            str(market.id) if str(prompt.values["provider"]) == "binance" else None
        ),
        interval="1d" if prompt.values["data_kind"] == "bar" else None,
    )


def preview_file_action(prompt: MarketFilePromptState) -> dict[str, Any]:
    return {"status": "preview", **prompt.summary()}


def selected_market_actions(market: Any) -> tuple[ActionItem, ...]:
    """Return observation and diagnostic actions valid for the selected market."""

    kinds = {
        "equity": (
            ("quote", "最新报价"),
            ("order-book", "订单簿"),
            ("trade", "最近成交"),
            ("bar", "最新分钟 K"),
        ),
        "spot": (
            ("quote", "最新报价"),
            ("order-book", "订单簿"),
            ("trade", "最近成交"),
            ("bar", "最新 K 线"),
        ),
        "perpetual": (
            ("quote", "最新报价"),
            ("order-book", "订单簿"),
            ("trade", "最近成交"),
            ("bar", "最新 K 线"),
        ),
        "future": (
            ("quote", "最新报价"),
            ("order-book", "订单簿"),
            ("trade", "最近成交"),
            ("bar", "最新 K 线"),
        ),
        "option": (
            ("quote", "最新报价"),
            ("order-book", "订单簿"),
            ("trade", "最近成交"),
            ("bar", "最新 K 线"),
            ("option-greeks", "Greeks"),
        ),
    }.get(str(market.instrument_kind), ())
    actions = [
        ActionItem(kind, label, f"读取当前标的的{label}", str(index))
        for index, (kind, label) in enumerate(kinds, 1)
    ]
    actions.extend(
        (
            ActionItem("validate", "验证市场定义", "检查标识、类型与交易规则", "v"),
            ActionItem(
                "universe",
                "检查 Reference 映射",
                "查看同类型可映射市场",
                "u",
            ),
        )
    )
    return tuple(actions)


MARKET_CONTROL_ACTIONS = (
    ActionItem("refresh", "刷新当前行情", "通过上次数据源立即刷新", "r"),
    ActionItem("watch", "开启或暂停自动刷新", "持续更新当前行情快照", "w"),
    ActionItem("save-snapshot", "保存当前快照", "将当前行情保留到活动历史", "s"),
)


def provider_actions(routes: tuple[MarketRouteView, ...]) -> tuple[ActionItem, ...]:
    return tuple(
        ActionItem(
            route.provider or str(index),
            route.provider or "未知 Provider",
            route.description,
            str(index),
        )
        for index, route in enumerate(routes, 1)
    )


def load_routes(
    state: Any, market: Any, observation_kind: str
) -> tuple[MarketRouteView, ...]:
    payload = MarketCliApplication(state.owner).routes(
        market_type=str(market.instrument_kind),
        observation_kind=observation_kind,
    )
    raw = payload.get("routes", [])
    return tuple(
        MarketRouteView.from_mapping(value)
        for value in raw
        if isinstance(value, Mapping)
    )


def load_observation(
    state: Any,
    market: Any,
    observation_kind: str,
    provider: str,
) -> dict[str, Any]:
    symbol = market.venue_symbol or market.instrument.display_symbol
    return MarketCliApplication(state.owner).once(
        market_id=str(market.id),
        instrument_id=str(market.instrument.id),
        exchange_id=str(market.exchange_id).rsplit(":", 1)[-1],
        market_type=str(market.instrument_kind),
        symbol=str(symbol),
        provider=provider,
        observation_kind=observation_kind,
    )


def run_diagnostic(state: Any, market: Any, action: str) -> dict[str, Any]:
    application = MarketCliApplication(state.owner)
    symbol = market.venue_symbol or market.instrument.display_symbol
    if action == "validate":
        return application.validate(
            market_id=str(market.id),
            instrument_id=str(market.instrument.id),
            exchange_id=str(market.exchange_id).rsplit(":", 1)[-1],
            market_type=str(market.instrument_kind),
            symbol=str(symbol),
        )
    if action == "universe":
        return application.reference_universe(
            instrument_kind=str(market.instrument_kind), limit=10_000
        )
    raise ValueError(f"unknown Market diagnostic: {action}")


def load_datasets(state: Any) -> dict[str, Any]:
    return MarketCliApplication(state.owner).datasets()


def observation_renderable(value: Any) -> RenderableType:
    if not isinstance(value, Mapping):
        return Pretty(value, expand_all=True)
    data_type = str(value.get("data_type") or "行情")
    symbol = str(value.get("symbol") or "—")
    provider = str(value.get("provider") or "—")
    if data_type in {"order-book", "order_book"}:
        return _order_book_renderable(value, symbol=symbol, provider=provider)
    rows = Table.grid(padding=(0, 2))
    rows.add_column(style="dim", no_wrap=True)
    rows.add_column(style="bold")
    fields = {
        "quote": (
            ("买价", "bid_price"),
            ("买量", "bid_quantity"),
            ("卖价", "ask_price"),
            ("卖量", "ask_quantity"),
            ("最新", "last_price"),
        ),
        "trade": (("成交价", "price"), ("成交量", "quantity")),
        "bar": (
            ("开", "open"),
            ("高", "high"),
            ("低", "low"),
            ("收", "close"),
            ("成交量", "volume"),
        ),
    }.get(data_type)
    if fields is None:
        return Pretty(dict(value), expand_all=True)
    for label, key in fields:
        field = value.get(key)
        if field is not None:
            rows.add_row(label, str(field))
    heading = Text()
    heading.append(symbol, style="bold cyan")
    heading.append(f"   {data_type.upper()}", style="dim")
    metadata = Text(f"{provider}  ·  {_observation_time(value)}", style="dim")
    return Panel(
        Group(heading, Text(""), rows, Text(""), metadata),
        border_style="cyan",
        padding=(1, 2),
    )


def _order_book_renderable(
    value: Mapping[str, Any], *, symbol: str, provider: str
) -> RenderableType:
    bids = tuple(value.get("bids") or ())
    asks = tuple(value.get("asks") or ())
    book = Table(show_header=True, header_style="bold")
    book.add_column("买价", justify="right", style="green")
    book.add_column("买量", justify="right")
    book.add_column("卖价", justify="right", style="red")
    book.add_column("卖量", justify="right")
    for index in range(max(len(bids), len(asks))):
        bid = bids[index] if index < len(bids) else ()
        ask = asks[index] if index < len(asks) else ()
        book.add_row(
            str(bid[0]) if len(bid) > 0 else "—",
            str(bid[1]) if len(bid) > 1 else "—",
            str(ask[0]) if len(ask) > 0 else "—",
            str(ask[1]) if len(ask) > 1 else "—",
        )
    heading = Text()
    heading.append(symbol, style="bold cyan")
    heading.append("   ORDER BOOK", style="dim")
    metadata = Text(f"{provider}  ·  {_observation_time(value)}", style="dim")
    return Panel(
        Group(heading, Text(""), book, Text(""), metadata),
        border_style="cyan",
        padding=(1, 2),
    )


def route_diagnostic_renderable(
    state: Any, market: Any, observation_kind: str
) -> RenderableType:
    market_type = str(market.instrument_kind)
    manifest = getattr(getattr(state.owner, "paths", None), "manifest", None)
    if manifest is None:
        manifest = getattr(getattr(state.owner, "paths", None), "root", "—")
    details = Table.grid(padding=(0, 1))
    details.add_column(style="bold cyan", no_wrap=True)
    details.add_column()
    details.add_row("请求", f"{market_type} / {observation_kind}")
    details.add_row("Workspace", str(manifest))
    details.add_row("检查", "确认 Market provider 已启用并支持该行情类型。")
    details.add_row("下一步", "/back 返回标的菜单；/home 返回首页。")
    return Panel(details, title="没有可用数据源", border_style="yellow")


def _observation_time(value: Mapping[str, Any]) -> str:
    raw = value.get("observed_at_unix_nanos") or value.get("event_at_unix_nanos")
    if raw is None:
        return "时间未知"
    try:
        instant = datetime.fromtimestamp(int(raw) / 1_000_000_000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return str(raw)
    return instant.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _history_time_millis(value: str, *, end_of_day: bool) -> int:
    parsed = date.fromisoformat(value)
    wall_time = time.max if end_of_day else time.min
    instant = datetime.combine(parsed, wall_time, tzinfo=timezone.utc)
    return int(instant.timestamp() * 1_000)


def _safe_filename(value: str) -> str:
    return "".join(character if character.isalnum() else "-" for character in value)


__all__ = [
    "MARKET_CONTROL_ACTIONS",
    "MarketFilePromptState",
    "MarketRouteView",
    "execute_file_action",
    "load_observation",
    "load_datasets",
    "load_routes",
    "observation_renderable",
    "provider_actions",
    "preview_file_action",
    "route_diagnostic_renderable",
    "run_diagnostic",
    "selected_market_actions",
]
