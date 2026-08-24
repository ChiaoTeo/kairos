"""Reference catalog discovery screens."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import Screen
from rich.pretty import Pretty
from textual.widgets import DataTable, Footer, Input, Label, OptionList, RichLog, Static
from textual.worker import Worker

from kairospy.application.reference import ReferenceApplication
from kairospy.infrastructure.contracts.reference import ReferenceClient

from ..dialogs import SelectDialog, SelectOption
from ..widgets import ActionItem, ActionList, WorkspaceHeader


REFERENCE_ACTIONS = (
    ActionItem("assets", "查找资产", "货币、股票及其他可计价资产", "1"),
    ActionItem("exchanges", "查找交易所", "浏览交易场所及其状态", "2"),
    ActionItem("instruments", "查找合约", "股票、现货、期货、期权与指数", "3"),
    ActionItem("markets", "查找交易标的", "按代码、名称或市场标识搜索", "4"),
    ActionItem("option-chain", "查看期权链", "按标的合约查看有效期权", "5"),
)

INSTRUMENT_TYPES = (
    SelectOption("全部类型", ""),
    SelectOption("股票", "equity"),
    SelectOption("现货", "spot"),
    SelectOption("永续合约", "perpetual"),
    SelectOption("期货", "future"),
    SelectOption("期权", "option"),
    SelectOption("指数", "index"),
)


class ReferenceScreen(Screen[None]):
    """Browse Reference without leaving the Textual application."""

    TITLE = "Kairos"
    SUB_TITLE = "首页 › 市场标的"
    BINDINGS = [
        Binding("escape", "back", "返回"),
        Binding("slash", "focus_search", "搜索"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._kind = ""
        self._instrument_type: str | None = None
        self._records: dict[str, Any] = {}

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("查找市场标的", id="page-title")
        yield ActionList(*REFERENCE_ACTIONS, id="reference-actions")
        with Container(id="reference-search-panel"):
            yield Label("", id="reference-search-title")
            yield Input(
                placeholder="输入代码或名称；直接回车浏览", id="reference-search"
            )
            yield Label("输入关键字，然后按 Enter 搜索。", id="reference-status")
            yield DataTable(
                id="reference-results", cursor_type="row", zebra_stripes=True
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#reference-search-panel", Container).display = False

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        kind = event.option.id
        if kind is None:
            return
        if kind == "instruments":
            self.app.push_screen(
                SelectDialog("选择合约类型", INSTRUMENT_TYPES),
                self._instrument_type_selected,
            )
            return
        self._open_search(kind)

    def _instrument_type_selected(self, value: str | None) -> None:
        if value is None:
            return
        self._instrument_type = value or None
        self._open_search("instruments")

    def _open_search(self, kind: str) -> None:
        self._kind = kind
        self.query_one("#reference-actions", ActionList).display = False
        panel = self.query_one("#reference-search-panel", Container)
        panel.display = True
        titles = {
            "assets": "查找资产",
            "exchanges": "查找交易所",
            "instruments": "查找合约",
            "markets": "查找交易标的",
            "option-chain": "查看期权链",
        }
        self.query_one("#reference-search-title", Label).update(titles[kind])
        search = self.query_one("#reference-search", Input)
        search.placeholder = (
            "输入标的合约 ID"
            if kind == "option-chain"
            else "输入代码或名称；直接回车浏览"
        )
        self._configure_table(kind)
        search.focus()

    def _configure_table(self, kind: str) -> None:
        table = self.query_one("#reference-results", DataTable)
        table.clear(columns=True)
        columns = {
            "assets": ("代码", "名称", "类别", "状态"),
            "exchanges": ("交易所", "名称", "状态"),
            "instruments": ("代码", "名称", "类型", "状态"),
            "markets": ("代码", "交易所", "类型", "状态"),
            "option-chain": ("代码", "到期时间", "执行价", "方向", "状态"),
        }[kind]
        table.add_columns(*columns)

    def action_focus_search(self) -> None:
        if not self.query_one("#reference-search-panel", Container).display:
            self._open_search("markets")
        self.query_one("#reference-search", Input).focus()

    def action_back(self) -> None:
        panel = self.query_one("#reference-search-panel", Container)
        if panel.display:
            panel.display = False
            self.query_one("#reference-actions", ActionList).display = True
            self.query_one("#reference-actions", ActionList).focus()
            return
        self.app.pop_screen()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "reference-search":
            return
        query = event.value.strip()
        if self._kind == "option-chain" and not query:
            self.query_one("#reference-status", Label).update("请输入标的合约 ID。")
            return
        self.query_one("#reference-status", Label).update("正在查询 Reference…")
        self.run_worker(
            lambda: self._find_records(query),
            name="reference-search",
            group="reference-search",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _application(self) -> ReferenceApplication:
        state = self.app.state  # type: ignore[attr-defined]
        if state.owner is None:
            raise RuntimeError(state.load_error or "当前没有可用的 workspace")
        return ReferenceApplication(
            ReferenceClient(database_path=state.owner.paths.reference_database())
        )

    def _find_records(self, query: str) -> tuple[Any, ...]:
        application = self._application()
        if self._kind == "assets":
            return application.find_assets(
                query=query or None, active_only=True, limit=25
            )
        if self._kind == "exchanges":
            return application.find_exchanges(
                query=query or None, active_only=True, limit=25
            )
        if self._kind == "instruments":
            return application.find_instruments(
                query=query or None,
                instrument_type=self._instrument_type,
                active_only=True,
                limit=25,
            )
        if self._kind == "markets":
            return application.find_markets(
                query=query or None, active_only=True, limit=25
            )
        if self._kind == "option-chain":
            return application.option_chain(query, active_only=True, limit=100)
        raise RuntimeError(f"unknown reference kind: {self._kind}")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "reference-search":
            return
        if event.state.name == "SUCCESS":
            result = event.worker.result
            self._show_results(tuple(result) if result is not None else ())
        elif event.state.name == "ERROR":
            self.query_one("#reference-status", Label).update(
                f"查询失败：{event.worker.error}"
            )

    def _show_results(self, records: tuple[Any, ...]) -> None:
        table = self.query_one("#reference-results", DataTable)
        table.clear()
        self._records = {str(record.id): record for record in records}
        for record in records:
            table.add_row(*self._row(record), key=str(record.id))
        self.query_one("#reference-status", Label).update(
            "没有找到匹配的记录。" if not records else f"找到 {len(records)} 条记录"
        )
        if records:
            table.focus()

    def _row(self, record: Any) -> tuple[str, ...]:
        if self._kind == "assets":
            return (
                record.code,
                record.name or "—",
                record.asset_class,
                str(record.status),
            )
        if self._kind == "exchanges":
            return str(record.id).rsplit(":", 1)[-1], record.name, str(record.status)
        if self._kind in {"instruments", "option-chain"}:
            if self._kind == "option-chain":
                return (
                    record.symbol,
                    str(record.expiry_unix_nanos or "—"),
                    str(record.strike or "—"),
                    record.option_right or "—",
                    str(record.status),
                )
            return (
                record.symbol,
                record.name or "—",
                record.instrument_type,
                str(record.status),
            )
        return (
            record.venue_symbol or record.instrument.display_symbol,
            str(record.exchange_id).rsplit(":", 1)[-1],
            record.instrument_kind,
            str(record.status),
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "reference-results":
            return
        record = self._records.get(str(event.row_key.value))
        if record is not None:
            self.app.push_screen(ReferenceDetailScreen(self._kind, record))


class ReferenceDetailScreen(Screen[None]):
    """Compact, type-independent detail view for a Reference record."""

    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, kind: str, record: Any) -> None:
        super().__init__()
        self.kind = kind
        self.record = record
        label = _record_label(record)
        self.sub_title = f"首页 › 市场标的 › {label}"

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label(_record_label(self.record), id="page-title")
        yield Static(_record_summary(self.record), id="reference-detail")
        yield ActionList(*self._actions(), id="reference-detail-actions")
        yield Label("选择可查看的关联信息。", id="reference-detail-status")
        yield RichLog(id="reference-related-result", wrap=True, highlight=False)
        yield Footer()

    def _actions(self) -> tuple[ActionItem, ...]:
        if self.kind == "assets":
            return (
                ActionItem("markets", "相关市场", "查看使用该资产的有效市场", "1"),
                ActionItem("technical", "技术标识", "查看完整 Asset ID", "2"),
            )
        if self.kind in {"instruments", "option-chain"}:
            return (
                ActionItem("listings", "上市信息", "查看交易所 listing", "1"),
                ActionItem("markets", "具体市场", "查看可交易市场", "2"),
                ActionItem("technical", "技术标识", "查看完整 Instrument ID", "3"),
            )
        if self.kind == "exchanges":
            return (
                ActionItem("listings", "上市信息", "查看该交易所的 listings", "1"),
                ActionItem("technical", "技术标识", "查看完整 Exchange ID", "2"),
            )
        return (ActionItem("technical", "技术标识", "查看完整 Market 标识", "1"),)

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        if action is None:
            return
        if action == "technical":
            self._show_related(
                {"technical_id": str(self.record.id), "record": self.record}
            )
            return
        self.query_one("#reference-detail-status", Label).update("正在读取关联目录…")
        self.run_worker(
            lambda: self._load_related(action),
            name="reference-related",
            group="reference-related",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _application(self) -> ReferenceApplication:
        state = self.app.state  # type: ignore[attr-defined]
        return ReferenceApplication(
            ReferenceClient(database_path=state.owner.paths.reference_database())
        )

    def _load_related(self, action: str) -> tuple[Any, ...]:
        application = self._application()
        if self.kind == "assets":
            return application.find_markets(
                asset_code=self.record.code, active_only=True, limit=25
            )
        if self.kind in {"instruments", "option-chain"}:
            if action == "listings":
                return application.find_listings(
                    instrument_id=self.record.id, active_only=True, limit=25
                )
            return application.find_markets(
                instrument_id=self.record.id, active_only=True, limit=25
            )
        return application.find_listings(
            exchange=self.record.id, active_only=True, limit=25
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "reference-related":
            return
        if event.state.name == "ERROR":
            self.query_one("#reference-detail-status", Label).update(
                f"读取失败：{event.worker.error}"
            )
        elif event.state.name == "SUCCESS":
            values = tuple(event.worker.result or ())
            self.query_one("#reference-detail-status", Label).update(
                "没有关联记录。" if not values else f"找到 {len(values)} 条关联记录"
            )
            self._show_related(values)

    def _show_related(self, value: Any) -> None:
        log = self.query_one("#reference-related-result", RichLog)
        log.clear()
        log.write(Pretty(value, expand_all=True))


def _record_label(record: Any) -> str:
    return str(
        getattr(record, "symbol", None)
        or getattr(record, "code", None)
        or getattr(record, "venue_symbol", None)
        or getattr(record, "name", None)
        or getattr(record, "id", "记录")
    )


def _record_summary(record: Any) -> str:
    if not is_dataclass(record):
        return str(record)
    lines: list[str] = []
    for field in fields(record):
        value = getattr(record, field.name)
        if value is not None:
            lines.append(f"{field.name.replace('_', ' ')}  {value}")
    return "\n".join(lines)
