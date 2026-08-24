"""Market discovery and direct observation screens."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Label, OptionList, RichLog
from textual.worker import Worker

from kairospy.investment.apps.market.application.cli import MarketCliApplication
from kairospy.investment.apps.reference.application import ReferenceApplication

from ..dialogs import ConfirmDialog, InputDialog, SelectDialog, SelectOption
from ..widgets import ActionItem, ActionList, WorkspaceHeader


MARKET_ACTIONS = (
    ActionItem("search", "搜索标的并查看行情", "按代码或名称搜索有效标的", "1"),
    ActionItem("download", "下载历史行情", "选择时间范围并保存行情数据", "2"),
    ActionItem("datasets", "查看本地行情数据", "浏览已准备的数据集", "3"),
    ActionItem("replay", "回放本地行情", "将 JSONL 行情事件送入独立回放", "r"),
    ActionItem("connected", "连接运行中的行情服务", "查看实时服务和订阅状态", "c"),
    ActionItem("diagnostics", "诊断问题", "检查市场定义和 Reference 映射", "d"),
    ActionItem("advanced", "高级市场标识", "手动输入完整市场标识", "a"),
)


class MarketScreen(Screen[None]):
    TITLE = "Kairos"
    SUB_TITLE = "首页 › 市场行情"
    BINDINGS = [
        Binding("escape", "back", "返回"),
        Binding("slash", "search", "搜索"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._markets: dict[str, Any] = {}
        self._search_purpose = "observe"

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("行情中心", id="page-title")
        yield ActionList(*MARKET_ACTIONS, id="market-actions")
        with Container(id="market-search-panel"):
            yield Input(
                placeholder="输入代码或名称；直接回车浏览有效标的",
                id="market-search",
            )
            yield Label("输入代码或名称，然后按 Enter 搜索。", id="market-status")
            yield DataTable(id="market-results", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#market-results", DataTable)
        table.add_columns("代码", "交易所", "类型", "计价资产", "状态")
        self.query_one("#market-search-panel", Container).display = False

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        if action == "search":
            self._search_purpose = "observe"
            self.action_search()
            return
        if action == "download":
            self._search_purpose = "download"
            self.action_search()
        elif action == "datasets":
            self._load_datasets()
        elif action == "replay":
            self._search_purpose = "replay"
            self.action_search()
        elif action == "connected":
            self.app.push_screen(ConnectedMarketScreen())
        elif action == "diagnostics":
            self._search_purpose = "diagnostics"
            self.action_search()
        elif action == "advanced":
            self.app.push_screen(
                InputDialog(
                    "输入完整市场 ID（将从 Reference 精确查找）",
                    placeholder="market:...",
                ),
                self._advanced_market_selected,
            )

    def action_search(self) -> None:
        self.query_one("#market-actions", ActionList).display = False
        panel = self.query_one("#market-search-panel", Container)
        panel.display = True
        self.query_one("#market-search", Input).focus()

    def _advanced_market_selected(self, value: str | None) -> None:
        if value is None:
            return
        self._search_purpose = "observe"
        self.action_search()
        search = self.query_one("#market-search", Input)
        search.value = value
        self.query_one("#market-status", Label).update("正在精确查找市场标识…")
        self.run_worker(
            lambda: self._find_markets(value),
            name="market-search",
            group="market-search",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _load_datasets(self) -> None:
        self.query_one("#market-actions", ActionList).display = False
        self.query_one("#market-search-panel", Container).display = True
        self.query_one("#market-status", Label).update("正在读取本地行情数据…")
        self.run_worker(
            lambda: MarketCliApplication(self.app.state.owner).datasets(),  # type: ignore[attr-defined]
            name="market-datasets",
            group="market-datasets",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def action_back(self) -> None:
        panel = self.query_one("#market-search-panel", Container)
        if panel.display:
            panel.display = False
            self.query_one("#market-actions", ActionList).display = True
            self.query_one("#market-actions", ActionList).focus()
            return
        self.app.pop_screen()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "market-search":
            return
        query = event.value.strip()
        self.query_one("#market-status", Label).update("正在搜索标的…")
        self.run_worker(
            lambda: self._find_markets(query),
            name="market-search",
            group="market-search",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _find_markets(self, query: str) -> tuple[Any, ...]:
        state = self.app.state  # type: ignore[attr-defined]
        if state.owner is None:
            raise RuntimeError(state.load_error or "当前没有可用的 workspace")
        application = ReferenceApplication.from_database(
            state.owner.paths.reference_database()
        )
        return application.find_markets(
            query=query or None,
            active_only=True,
            limit=25,
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group == "market-datasets":
            if event.state.name == "SUCCESS":
                self.app.push_screen(
                    MarketResultScreen("本地行情数据", event.worker.result)
                )
                self.query_one("#market-search-panel", Container).display = False
                self.query_one("#market-actions", ActionList).display = True
            elif event.state.name == "ERROR":
                self.query_one("#market-status", Label).update(
                    f"读取失败：{event.worker.error}"
                )
            return
        if event.worker.group != "market-search":
            return
        if event.state.name == "SUCCESS":
            result = event.worker.result
            self._show_results(tuple(result) if result is not None else ())
        elif event.state.name == "ERROR":
            self.query_one("#market-status", Label).update(
                f"搜索失败：{event.worker.error}"
            )

    def _show_results(self, markets: tuple[Any, ...]) -> None:
        table = self.query_one("#market-results", DataTable)
        table.clear()
        self._markets = {str(market.id): market for market in markets}
        for market in markets:
            table.add_row(
                market.venue_symbol or market.instrument.display_symbol,
                str(market.exchange_id).rsplit(":", 1)[-1],
                str(market.instrument_kind),
                market.quote_asset or "—",
                str(market.status),
                key=str(market.id),
            )
        status = (
            "没有找到匹配的有效标的。" if not markets else f"找到 {len(markets)} 个标的"
        )
        self.query_one("#market-status", Label).update(status)
        if markets:
            table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "market-results":
            return
        market = self._markets.get(str(event.row_key.value))
        if market is None:
            return
        state = self.app.state  # type: ignore[attr-defined]
        state.selected_market = market
        if self._search_purpose == "download":
            self.app.push_screen(MarketHistoryScreen(market))
        elif self._search_purpose == "replay":
            self.app.push_screen(MarketReplayScreen(market))
        elif self._search_purpose == "diagnostics":
            self.app.push_screen(MarketDiagnosticsScreen(market))
        else:
            self.app.push_screen(MarketDetailScreen(market))


class MarketDetailScreen(Screen[None]):
    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, market: Any) -> None:
        super().__init__()
        self.market = market
        self._pending_observation: str | None = None
        self._routes: tuple[dict[str, Any], ...] = ()
        symbol = market.venue_symbol or market.instrument.display_symbol
        self.sub_title = f"首页 › 市场行情 › {symbol}"

    def compose(self) -> ComposeResult:
        symbol = self.market.venue_symbol or self.market.instrument.display_symbol
        yield WorkspaceHeader()
        yield Label(symbol, id="page-title")
        yield Label(
            f"{str(self.market.exchange_id).rsplit(':', 1)[-1]}  ·  "
            f"{self.market.instrument_kind}  ·  {self.market.quote_asset or '—'}",
            id="workspace-summary",
        )
        yield ActionList(*self._observation_actions(), id="observation-actions")
        yield Label(
            "选择要查看的行情。",
            id="observation-status",
            classes="status-line",
        )
        yield RichLog(id="observation-result", wrap=True, highlight=False)
        yield Footer()

    def _observation_actions(self) -> tuple[ActionItem, ...]:
        kinds = {
            "equity": (
                ("quote", "最新报价"),
                ("trade", "最近成交"),
                ("bar", "最新分钟 K"),
            ),
            "spot": (
                ("quote", "最新报价"),
                ("trade", "最近成交"),
                ("bar", "最新 K 线"),
                ("order-book", "买卖盘口"),
            ),
            "perpetual": (
                ("quote", "最新报价"),
                ("trade", "最近成交"),
                ("bar", "最新 K 线"),
                ("order-book", "买卖盘口"),
            ),
            "future": (
                ("quote", "最新报价"),
                ("trade", "最近成交"),
                ("bar", "最新 K 线"),
                ("order-book", "买卖盘口"),
            ),
            "option": (
                ("quote", "最新报价"),
                ("trade", "最近成交"),
                ("bar", "最新 K 线"),
                ("order-book", "买卖盘口"),
                ("option-greeks", "Greeks"),
            ),
        }.get(str(self.market.instrument_kind), ())
        return tuple(
            ActionItem(kind, label, f"读取当前标的的 {label}", str(index))
            for index, (kind, label) in enumerate(kinds, 1)
        )

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is None:
            return
        self._pending_observation = event.option.id
        self.query_one("#observation-status", Label).update("正在读取可用数据源…")
        self.run_worker(
            lambda: self._load_routes(event.option.id or ""),
            name="market-routes",
            group="market-routes",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _load_routes(self, observation_kind: str) -> tuple[dict[str, Any], ...]:
        state = self.app.state  # type: ignore[attr-defined]
        payload = MarketCliApplication(state.owner).routes(
            market_type=str(self.market.instrument_kind),
            observation_kind=observation_kind,
        )
        raw = payload.get("routes", [])
        return tuple(dict(value) for value in raw if isinstance(value, Mapping))

    def _load_observation(self, provider: str) -> dict[str, Any]:
        state = self.app.state  # type: ignore[attr-defined]
        symbol = self.market.venue_symbol or self.market.instrument.display_symbol
        return MarketCliApplication(state.owner).once(
            market_id=str(self.market.id),
            instrument_id=str(self.market.instrument.id),
            exchange_id=str(self.market.exchange_id).rsplit(":", 1)[-1],
            market_type=str(self.market.instrument_kind),
            symbol=str(symbol),
            provider=provider,
            observation_kind=self._pending_observation or "quote",
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.state.name == "ERROR" and event.worker.group in {
            "market-routes",
            "market-observation",
        }:
            self.query_one("#observation-status", Label).update(
                f"读取失败：{event.worker.error}"
            )
            return
        if event.state.name != "SUCCESS":
            return
        if event.worker.group == "market-routes":
            result = event.worker.result
            self._routes = tuple(result) if result is not None else ()
            if not self._routes:
                self._show_route_diagnostics()
            elif len(self._routes) == 1:
                self._request_observation(str(self._routes[0].get("provider")))
            else:
                self.app.push_screen(
                    SelectDialog(
                        "选择行情数据源",
                        tuple(
                            SelectOption(
                                str(route.get("provider")), str(route.get("provider"))
                            )
                            for route in self._routes
                        ),
                    ),
                    self._provider_selected,
                )
        elif event.worker.group == "market-observation":
            self.query_one("#observation-status", Label).update("行情已更新")
            log = self.query_one("#observation-result", RichLog)
            log.clear()
            log.write(_observation_renderable(event.worker.result))

    def _show_route_diagnostics(self) -> None:
        market_type = str(self.market.instrument_kind)
        observation_kind = self._pending_observation or "quote"
        state = self.app.state  # type: ignore[attr-defined]
        manifest = getattr(getattr(state.owner, "paths", None), "manifest", None)
        if manifest is None:
            manifest = getattr(getattr(state.owner, "paths", None), "root", "—")
        self.query_one("#observation-status", Label).update(
            f"没有可用数据源 · {market_type} / {observation_kind}"
        )
        details = Table.grid(padding=(0, 1))
        details.add_column(style="bold cyan", no_wrap=True)
        details.add_column()
        details.add_row("请求", f"{market_type} / {observation_kind}")
        details.add_row("Workspace", str(manifest))
        details.add_row(
            "检查",
            "确认 [[market.providers]] 已启用，且 provider 支持该行情类型。",
        )
        details.add_row("下一步", "返回首页 → 管理运行资源 → 行情数据")
        log = self.query_one("#observation-result", RichLog)
        log.clear()
        log.write(Panel(details, title="数据源诊断", border_style="yellow"))

    def _provider_selected(self, provider: str | None) -> None:
        if provider is not None:
            self._request_observation(provider)

    def _request_observation(self, provider: str) -> None:
        self.query_one("#observation-status", Label).update(
            f"正在通过 {provider} 读取行情…"
        )
        self.run_worker(
            lambda: self._load_observation(provider),
            name="market-observation",
            group="market-observation",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )


def _observation_renderable(value: Any) -> RenderableType:
    if not isinstance(value, Mapping):
        return Pretty(value, expand_all=True)
    data_type = str(value.get("data_type") or "行情")
    symbol = str(value.get("symbol") or "—")
    provider = str(value.get("provider") or "—")
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


def _observation_time(value: Mapping[str, Any]) -> str:
    raw = value.get("observed_at_unix_nanos") or value.get("event_at_unix_nanos")
    if raw is None:
        return "时间未知"
    try:
        instant = datetime.fromtimestamp(int(raw) / 1_000_000_000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return str(raw)
    return instant.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


class MarketResultScreen(Screen[None]):
    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, title: str, value: Any) -> None:
        super().__init__()
        self.result_title = title
        self.value = value
        self.sub_title = f"首页 › 市场行情 › {title}"

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label(self.result_title, id="page-title")
        yield RichLog(id="market-generic-result", wrap=True, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(RichLog).write(Pretty(self.value, expand_all=True))

    def action_back(self) -> None:
        self.app.pop_screen()


class MarketDiagnosticsScreen(Screen[None]):
    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, market: Any) -> None:
        super().__init__()
        self.market = market
        symbol = market.venue_symbol or market.instrument.display_symbol
        self.sub_title = f"首页 › 市场行情 › 诊断 › {symbol}"

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("Market 诊断", id="page-title")
        yield ActionList(
            ActionItem("validate", "验证市场定义", "检查标识、类型与交易规则", "1"),
            ActionItem(
                "universe", "检查 Reference → Market 映射", "查看同类型可映射市场", "2"
            ),
            id="market-diagnostic-actions",
        )
        yield Label("选择诊断。", id="market-diagnostic-status")
        yield RichLog(id="market-diagnostic-result", wrap=True, highlight=False)
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is None:
            return
        self.query_one("#market-diagnostic-status", Label).update("正在诊断…")
        self.run_worker(
            lambda: self._execute(event.option.id or ""),
            name="market-diagnostic",
            group="market-diagnostic",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _execute(self, action: str) -> dict[str, Any]:
        app = MarketCliApplication(self.app.state.owner)  # type: ignore[attr-defined]
        symbol = self.market.venue_symbol or self.market.instrument.display_symbol
        if action == "validate":
            return app.validate(
                market_id=str(self.market.id),
                instrument_id=str(self.market.instrument.id),
                exchange_id=str(self.market.exchange_id).rsplit(":", 1)[-1],
                market_type=str(self.market.instrument_kind),
                symbol=str(symbol),
            )
        return app.reference_universe(
            instrument_kind=str(self.market.instrument_kind), limit=10_000
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "market-diagnostic":
            return
        if event.state.name == "ERROR":
            self.query_one("#market-diagnostic-status", Label).update(
                f"诊断失败：{event.worker.error}"
            )
        elif event.state.name == "SUCCESS":
            self.query_one("#market-diagnostic-status", Label).update("诊断完成")
            log = self.query_one("#market-diagnostic-result", RichLog)
            log.clear()
            log.write(Pretty(event.worker.result, expand_all=True))


class MarketHistoryScreen(Screen[None]):
    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, market: Any) -> None:
        super().__init__()
        self.market = market
        self.provider = (
            "binance" if str(market.instrument_kind) == "spot" else "massive"
        )
        self.data_kind = "bar"
        self.start = (
            datetime.now(timezone.utc).date() - timedelta(days=30)
        ).isoformat()
        self.end = datetime.now(timezone.utc).date().isoformat()
        self.destination = ""
        symbol = market.venue_symbol or market.instrument.display_symbol
        self.sub_title = f"首页 › 市场行情 › 下载 › {symbol}"

    def compose(self) -> ComposeResult:
        symbol = self.market.venue_symbol or self.market.instrument.display_symbol
        yield WorkspaceHeader()
        yield Label(f"下载 {symbol} 历史行情", id="page-title")
        yield ActionList(
            ActionItem(
                "configure", "配置并下载", "选择数据类型、日期范围和保存位置", "1"
            ),
            id="history-actions",
        )
        yield Label(f"Provider：{self.provider}", id="history-status")
        yield RichLog(id="history-result", wrap=True, highlight=False)
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id != "configure":
            return
        options = (
            SelectOption("bar", "K 线"),
            SelectOption("quote", "报价"),
            SelectOption("trade", "成交"),
        )
        if self.provider == "massive":
            options = options[:2]
        self.app.push_screen(
            SelectDialog("选择历史行情类型", options), self._kind_selected
        )

    def _kind_selected(self, value: str | None) -> None:
        if value is None:
            return
        self.data_kind = value
        self.app.push_screen(
            InputDialog("开始日期", value=self.start), self._start_selected
        )

    def _start_selected(self, value: str | None) -> None:
        if value is None:
            return
        self.start = value
        self.app.push_screen(
            InputDialog("结束日期", value=self.end), self._end_selected
        )

    def _end_selected(self, value: str | None) -> None:
        if value is None:
            return
        self.end = value
        symbol = self.market.venue_symbol or self.market.instrument.display_symbol
        default = f"market-history/{_safe_filename(str(symbol))}-{self.data_kind}.jsonl"
        self.app.push_screen(
            InputDialog("保存位置", value=default), self._destination_selected
        )

    def _destination_selected(self, value: str | None) -> None:
        if value is None:
            return
        self.destination = value
        if self.app.state.yes:  # type: ignore[attr-defined]
            self._download()
            return
        self.app.push_screen(
            ConfirmDialog("下载历史行情", f"保存到 {value}", confirm_label="下载"),
            lambda confirmed: self._download() if confirmed else None,
        )

    def _download(self) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        if state.dry_run or state.no_exec:
            self._show({"status": "preview", "destination": self.destination})
            return
        self.query_one("#history-status", Label).update("正在下载；Ctrl+C 可取消等待…")
        self.run_worker(
            self._execute,
            name="market-download",
            group="market-download",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _execute(self) -> dict[str, Any]:
        symbol = self.market.venue_symbol or self.market.instrument.display_symbol
        return MarketCliApplication(self.app.state.owner).download(  # type: ignore[attr-defined]
            provider=self.provider,
            symbol=str(symbol),
            market_type=str(self.market.instrument_kind),
            data_kind=self.data_kind,
            instrument_id=str(self.market.instrument.id),
            start_unix_millis=_history_time_millis(self.start, end_of_day=False),
            end_unix_millis=_history_time_millis(self.end, end_of_day=True),
            destination=self.destination,
            market_id=str(self.market.id) if self.provider == "binance" else None,
            interval="1d" if self.data_kind == "bar" else None,
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "market-download":
            return
        if event.state.name == "ERROR":
            self.query_one("#history-status", Label).update(
                f"下载失败：{event.worker.error}"
            )
        elif event.state.name == "SUCCESS":
            self.query_one("#history-status", Label).update("下载完成")
            self._show(event.worker.result)

    def _show(self, value: Any) -> None:
        log = self.query_one("#history-result", RichLog)
        log.clear()
        log.write(Pretty(value, expand_all=True))


class MarketReplayScreen(Screen[None]):
    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, market: Any) -> None:
        super().__init__()
        self.market = market
        symbol = market.venue_symbol or market.instrument.display_symbol
        self.sub_title = f"首页 › 市场行情 › 回放 › {symbol}"
        self.files: tuple[str, ...] = ()

    def compose(self) -> ComposeResult:
        symbol = self.market.venue_symbol or self.market.instrument.display_symbol
        yield WorkspaceHeader()
        yield Label(f"回放 {symbol} 行情", id="page-title")
        yield ActionList(
            ActionItem("select", "选择事件文件", "可输入多个逗号分隔 JSONL 文件", "1"),
            id="replay-actions",
        )
        yield Label("回放在独立 Market 模式执行。", id="replay-status")
        yield RichLog(id="replay-result", wrap=True, highlight=False)
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id == "select":
            self.app.push_screen(
                InputDialog("行情事件文件（逗号分隔）"), self._files_selected
            )

    def _files_selected(self, value: str | None) -> None:
        if not value:
            return
        self.files = tuple(item.strip() for item in value.split(",") if item.strip())
        if not self.files:
            return
        if self.app.state.yes:  # type: ignore[attr-defined]
            self._run()
            return
        self.app.push_screen(
            ConfirmDialog(
                "开始行情回放",
                "\n".join(self.files),
                confirm_label="回放",
            ),
            lambda confirmed: self._run() if confirmed else None,
        )

    def _run(self) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        if state.dry_run or state.no_exec:
            self._show({"status": "preview", "files": self.files})
            return
        self.query_one("#replay-status", Label).update("正在回放；Ctrl+C 可取消等待…")
        self.run_worker(
            self._execute,
            name="market-replay",
            group="market-replay",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _execute(self) -> dict[str, Any]:
        symbol = self.market.venue_symbol or self.market.instrument.display_symbol
        return MarketCliApplication(self.app.state.owner).replay(  # type: ignore[attr-defined]
            market_id=str(self.market.id),
            instrument_id=str(self.market.instrument.id),
            exchange_id=str(self.market.exchange_id).rsplit(":", 1)[-1],
            market_type=str(self.market.instrument_kind),
            symbol=str(symbol),
            files=self.files,
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "market-replay":
            return
        if event.state.name == "ERROR":
            self.query_one("#replay-status", Label).update(
                f"回放失败：{event.worker.error}"
            )
        elif event.state.name == "SUCCESS":
            self.query_one("#replay-status", Label).update("回放完成")
            self._show(event.worker.result)

    def _show(self, value: Any) -> None:
        log = self.query_one("#replay-result", RichLog)
        log.clear()
        log.write(Pretty(value, expand_all=True))


class ConnectedMarketScreen(Screen[None]):
    TITLE = "Kairos"
    SUB_TITLE = "首页 › 市场行情 › 运行中服务"
    BINDINGS = [Binding("escape", "back", "返回")]

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("运行中的 Market 服务", id="page-title")
        yield ActionList(
            ActionItem("status", "查看状态", "读取服务健康与运行资源", "1"),
            ActionItem("routes", "查看数据路由", "读取已配置 Provider route", "2"),
            ActionItem("snapshot", "查看行情快照", "读取 Quote、K 线或 Greeks", "3"),
            ActionItem("freshness", "查看行情新鲜度", "读取指定 Market 数据年龄", "4"),
            ActionItem("start", "启动服务", "启动 Workspace Market 服务", "5"),
            ActionItem("stop", "停止服务", "停止 Workspace Market 服务", "6"),
            ActionItem("restart", "重启服务", "重启 Workspace Market 服务", "7"),
            ActionItem("logs", "查看日志", "读取最近 200 行服务日志", "8"),
            ActionItem("pause", "暂停行情回放", "暂停 replay 时钟", "p"),
            ActionItem("resume", "继续行情回放", "恢复 replay 时钟", "r"),
            id="connected-market-actions",
        )
        yield Label("选择操作。", id="connected-market-status")
        yield RichLog(id="connected-market-result", wrap=True, highlight=False)
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        if action is None:
            return
        if action in {"snapshot", "freshness"}:
            self._select_view(action)
            return
        if action in {"start", "stop", "restart", "pause", "resume"}:
            if self.app.state.yes:  # type: ignore[attr-defined]
                self._run(action)
                return
            self.app.push_screen(
                ConfirmDialog(
                    f"{action} Market",
                    "该操作会改变 Workspace Market 服务状态。",
                    confirm_label="继续",
                ),
                lambda confirmed: self._run(action) if confirmed else None,
            )
            return
        self._run(action)

    def _select_view(self, action: str) -> None:
        self._pending_action = action
        if action == "snapshot":
            self.app.push_screen(
                SelectDialog(
                    "选择快照类型",
                    (
                        SelectOption("quote", "Quote"),
                        SelectOption("bar", "K 线"),
                        SelectOption("greeks", "Greeks"),
                    ),
                ),
                self._kind_selected,
            )
        else:
            self._pending_kind = "freshness"
            self._ask_market_id()

    def _kind_selected(self, value: str | None) -> None:
        if value is None:
            return
        self._pending_kind = value
        self._ask_market_id()

    def _ask_market_id(self) -> None:
        selected = self.app.state.selected_market  # type: ignore[attr-defined]
        default = str(selected.id) if selected is not None else ""
        self.app.push_screen(
            InputDialog("Market ID", value=default, placeholder="market:..."),
            self._market_selected,
        )

    def _market_selected(self, value: str | None) -> None:
        if not value:
            return
        self._pending_market_id = value
        self.app.push_screen(
            InputDialog("Provider", placeholder="massive / binance"),
            self._provider_selected,
        )

    def _provider_selected(self, value: str | None) -> None:
        if not value:
            return
        self._pending_provider = value
        if self._pending_kind == "bar":
            self.app.push_screen(
                InputDialog("K 线周期", value="1m"), self._timeframe_selected
            )
        else:
            self._run(self._pending_action)

    def _timeframe_selected(self, value: str | None) -> None:
        if not value:
            return
        self._pending_timeframe = value
        self._run(self._pending_action)

    def _run(self, action: str) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        if action in {"start", "stop", "restart", "pause", "resume"} and (
            state.dry_run or state.no_exec
        ):
            self._show({"status": "preview", "action": action})
            return
        self.query_one("#connected-market-status", Label).update("正在读取服务…")
        self.run_worker(
            lambda: self._execute(action),
            name="connected-market",
            group="connected-market",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _execute(self, action: str) -> dict[str, Any]:
        from kairospy.system.apps.components.application import ComponentProcessApplication

        state = self.app.state  # type: ignore[attr-defined]
        processes = ComponentProcessApplication(state.owner)
        if action == "status":
            return processes.status("market")
        if action == "start":
            return processes.ensure_running("market").status()
        if action == "stop":
            from kairospy.system.apps.launch.application import (
                WorkspaceComponentDependencyApplication,
            )

            WorkspaceComponentDependencyApplication(state.owner).require_clear(
                "market", "stop"
            )
            return processes.stop("market")
        if action == "restart":
            from kairospy.system.apps.launch.application import (
                WorkspaceComponentDependencyApplication,
            )

            WorkspaceComponentDependencyApplication(state.owner).require_clear(
                "market", "restart"
            )
            return processes.restart("market").status()
        if action == "logs":
            return {"component": "market", "lines": list(processes.logs("market"))}
        market = MarketCliApplication(state.owner)
        if action == "snapshot":
            return market.connected_snapshot(
                market_id=self._pending_market_id,
                provider=self._pending_provider,
                kind=self._pending_kind,
                timeframe=getattr(self, "_pending_timeframe", None),
            )
        if action == "freshness":
            return market.connected_freshness(
                market_id=self._pending_market_id,
                provider=self._pending_provider,
            )
        client = processes.client("market", state.owner.paths.process_socket("market"))
        if action == "routes":
            return client.data_routes()  # type: ignore[attr-defined,no-any-return]
        if action == "pause":
            return client.pause_replay()  # type: ignore[attr-defined,no-any-return]
        return client.resume_replay()  # type: ignore[attr-defined,no-any-return]

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "connected-market":
            return
        if event.state.name == "ERROR":
            self.query_one("#connected-market-status", Label).update(
                f"操作失败：{event.worker.error}"
            )
        elif event.state.name == "SUCCESS":
            self.query_one("#connected-market-status", Label).update("操作完成")
            log = self.query_one("#connected-market-result", RichLog)
            log.clear()
            log.write(Pretty(event.worker.result, expand_all=True))

    def _show(self, value: Any) -> None:
        self.query_one("#connected-market-status", Label).update("操作完成")
        log = self.query_one("#connected-market-result", RichLog)
        log.clear()
        log.write(Pretty(value, expand_all=True))


def _history_time_millis(value: str, *, end_of_day: bool) -> int:
    normalized = value.strip()
    if normalized.isdigit():
        return int(normalized)
    try:
        parsed_date = date.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    return int(
        datetime.combine(
            parsed_date, time.max if end_of_day else time.min, tzinfo=timezone.utc
        ).timestamp()
        * 1000
    )


def _safe_filename(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in value
    ).strip("-")
    return cleaned or "market"
