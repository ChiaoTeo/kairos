"""Market discovery and observation actions for the Workbench slice."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from time import time_ns
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table
from rich.text import Text

from kairospy.investment.apps.market.application.cli import MarketCliApplication

from ....widgets import ActionItem
from ...presentation import ResultTone, conclusion, count, facts


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


def file_command(state: Any, prompt: MarketFilePromptState) -> tuple[str, ...]:
    """Return the canonical CLI command for a download or replay operation."""

    market = prompt.market
    symbol = market.venue_symbol or market.instrument.display_symbol
    if prompt.action == "replay":
        arguments: list[str] = [
            "standalone",
            "replay",
            "--market-id",
            str(market.id),
            "--instrument-id",
            str(market.instrument.id),
            "--exchange-id",
            str(market.exchange_id).rsplit(":", 1)[-1],
            "--market-type",
            str(market.instrument_kind),
            "--symbol",
            str(symbol),
        ]
        for path in prompt.values["files"]:
            arguments.extend(("--file", str(path)))
    else:
        provider = str(prompt.values["provider"])
        data_kind = str(prompt.values["data_kind"])
        arguments = [
            "standalone",
            "download",
            "--provider",
            provider,
            "--symbol",
            str(symbol),
            "--market-type",
            str(market.instrument_kind),
            "--data-kind",
            data_kind,
            "--instrument-id",
            str(market.instrument.id),
            "--start",
            str(_history_time_millis(str(prompt.values["start"]), end_of_day=False)),
            "--end",
            str(_history_time_millis(str(prompt.values["end"]), end_of_day=True)),
            "--file",
            str(prompt.values["destination"]),
        ]
        if provider == "binance":
            arguments.extend(("--market-id", str(market.id)))
        if data_kind == "bar":
            arguments.extend(("--interval", "1d"))
    return tuple(MarketCliApplication(state.owner).shell_command(arguments))


def preview_file_action(prompt: MarketFilePromptState) -> dict[str, Any]:
    return {"status": "preview", **prompt.summary()}


def file_result_renderable(
    result: Any, prompt: MarketFilePromptState
) -> RenderableType:
    details = Table.grid(padding=(0, 2))
    details.add_column(style="dim", no_wrap=True)
    details.add_column(style="dim")
    if prompt.action == "replay":
        files = tuple(prompt.values.get("files") or ())
        details.add_row("来源", "本地回放")
        details.add_row("输入", f"{len(files)} 个 JSONL 文件")
    else:
        provider = str(prompt.values.get("provider") or "—")
        details.add_row("来源", f"{provider} · Provider 直连")
        details.add_row("产物", str(prompt.values.get("destination") or "—"))
    result_rows: list[tuple[str, RenderableType]] = []
    if isinstance(result, Mapping):
        result_labels = {
            "status": "状态",
            "record_count": "记录数",
            "written_count": "已写入",
            "replayed_count": "已回放",
            "market_id": "Market ID",
            "provider": "Provider",
            "detail": "说明",
        }
        for key, label in result_labels.items():
            if key in result and result[key] is not None:
                value = result[key]
                result_rows.append(
                    (label, count(value) if isinstance(value, int) else str(value))
                )
    preview = isinstance(result, Mapping) and result.get("status") == "preview"
    return Group(
        conclusion(
            "Market 文件操作预演完成，未执行任何修改"
            if preview
            else "Market 文件操作已完成",
            tone=ResultTone.PREVIEW if preview else ResultTone.SUCCESS,
        ),
        facts(result_rows) if result_rows else Text("没有更多业务字段", style="dim"),
        Text(""),
        details,
    )


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
            ActionItem(
                "diagnose",
                "诊断当前市场",
                "检查标识、类型与交易规则",
                "d",
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


def route_command(state: Any, market: Any, observation_kind: str) -> tuple[str, ...]:
    """Return the canonical CLI command for standalone route discovery."""

    return tuple(
        MarketCliApplication(state.owner).shell_command(
            (
                "standalone",
                "routes",
                "--market-type",
                str(market.instrument_kind),
                "--observation-kind",
                observation_kind,
            )
        )
    )


def load_observation(
    state: Any,
    market: Any,
    observation_kind: str,
    provider: str,
) -> dict[str, Any]:
    symbol = market.venue_symbol or market.instrument.display_symbol
    result = MarketCliApplication(state.owner).once(
        market_id=str(market.id),
        instrument_id=str(market.instrument.id),
        exchange_id=str(market.exchange_id).rsplit(":", 1)[-1],
        market_type=str(market.instrument_kind),
        symbol=str(symbol),
        provider=provider,
        observation_kind=observation_kind,
    )
    return {
        **result,
        "_source_mode": "provider-direct",
        "_transport": "REST",
        "_fetched_at_unix_nanos": time_ns(),
    }


def observation_command(
    state: Any,
    market: Any,
    observation_kind: str,
    provider: str,
) -> tuple[str, ...]:
    """Return the canonical CLI command for one direct observation."""

    symbol = market.venue_symbol or market.instrument.display_symbol
    arguments = (
        "standalone",
        "once",
        "--market-id",
        str(market.id),
        "--instrument-id",
        str(market.instrument.id),
        "--exchange-id",
        str(market.exchange_id).rsplit(":", 1)[-1],
        "--market-type",
        str(market.instrument_kind),
        "--symbol",
        str(symbol),
        "--provider",
        provider,
        "--observation-kind",
        observation_kind,
    )
    return tuple(MarketCliApplication(state.owner).shell_command(arguments))


def run_diagnostic(state: Any, market: Any) -> dict[str, Any]:
    application = MarketCliApplication(state.owner)
    symbol = market.venue_symbol or market.instrument.display_symbol
    return application.validate(
        market_id=str(market.id),
        instrument_id=str(market.instrument.id),
        exchange_id=str(market.exchange_id).rsplit(":", 1)[-1],
        market_type=str(market.instrument_kind),
        symbol=str(symbol),
    )


def diagnostic_command(state: Any, market: Any) -> tuple[str, ...]:
    symbol = market.venue_symbol or market.instrument.display_symbol
    arguments = (
        "standalone",
        "validate",
        "--market-id",
        str(market.id),
        "--instrument-id",
        str(market.instrument.id),
        "--exchange-id",
        str(market.exchange_id).rsplit(":", 1)[-1],
        "--market-type",
        str(market.instrument_kind),
        "--symbol",
        str(symbol),
    )
    return tuple(MarketCliApplication(state.owner).shell_command(arguments))


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
    if data_type == "quote":
        rows.add_column(min_width=12, no_wrap=True)
        rows.add_column(min_width=12, no_wrap=True)
        rows.add_row(
            Text("买盘 BID", style="dim green"),
            Text("卖盘 ASK", style="dim red"),
        )
        rows.add_row(
            Text(str(value.get("bid_price") or "—"), style="bold green"),
            Text(str(value.get("ask_price") or "—"), style="bold red"),
        )
        rows.add_row(
            Text(f"数量  {value.get('bid_quantity') or '—'}", style="dim"),
            Text(f"数量  {value.get('ask_quantity') or '—'}", style="dim"),
        )
        if value.get("last_price") is not None:
            rows.add_row(
                Text(f"最新  {value['last_price']}", style="bold"),
                Text(""),
            )
    else:
        rows.add_column(style="dim", no_wrap=True)
        rows.add_column(style="bold")
        for label, key in fields:
            field = value.get(key)
            if field is not None:
                rows.add_row(label, str(field))
    heading = Text()
    heading.append(symbol, style="bold cyan")
    heading.append(f"   {data_type.upper()}", style="dim")
    metadata = _observation_metadata(value, provider)
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
    metadata = _observation_metadata(value, provider)
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
    raw = _observation_nanos(value)
    if raw is None:
        return "时间未知"
    try:
        instant = datetime.fromtimestamp(int(raw) / 1_000_000_000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return str(raw)
    return instant.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _observation_metadata(value: Mapping[str, Any], provider: str) -> RenderableType:
    details = Table.grid(padding=(0, 2))
    details.add_column(style="dim", no_wrap=True)
    details.add_column(style="dim")
    source_mode = {
        "provider-direct": "Provider 直连",
        "workspace-view": "Workspace 当前视图",
        "local-replay": "本地回放",
        "local-file": "本地文件",
        "derived": "派生结果",
    }.get(str(value.get("_source_mode") or ""), "来源模式未知")
    transport = str(value.get("_transport") or "").strip()
    source = " · ".join(part for part in (provider, transport, source_mode) if part)
    details.add_row("来源", source)
    observed = _observation_nanos(value)
    if observed is not None:
        details.add_row("市场时间", _format_unix_nanos(observed))
    fetched = value.get("_fetched_at_unix_nanos")
    if fetched is not None:
        details.add_row("获取时间", _format_unix_nanos(fetched))
    if observed is not None and fetched is not None:
        try:
            age_seconds = max(0.0, (int(fetched) - int(observed)) / 1_000_000_000)
        except (TypeError, ValueError):
            pass
        else:
            age = Text(_format_age(age_seconds))
            if age_seconds >= 30:
                age.append("  ⚠ 较旧", style="bold yellow")
            details.add_row("数据年龄", age)
    return details


def _observation_nanos(value: Mapping[str, Any]) -> Any:
    return (
        value.get("observed_at_unix_nanos")
        or value.get("event_at_unix_nanos")
        or value.get("opened_at_unix_nanos")
        or value.get("source_observed_at_unix_nanos")
    )


def _format_unix_nanos(raw: Any) -> str:
    try:
        instant = datetime.fromtimestamp(int(raw) / 1_000_000_000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return str(raw)
    return instant.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_age(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1_000:.0f} 毫秒"
    if seconds < 60:
        return f"{seconds:.1f} 秒"
    return f"{seconds / 60:.1f} 分钟"


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
    "diagnostic_command",
    "file_command",
    "file_result_renderable",
    "load_observation",
    "load_datasets",
    "load_routes",
    "observation_command",
    "observation_renderable",
    "provider_actions",
    "preview_file_action",
    "route_diagnostic_renderable",
    "route_command",
    "run_diagnostic",
    "selected_market_actions",
]
