"""Product-oriented workbench home."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Label, OptionList, Static

from ..widgets import ActionItem, ActionList, WorkspaceHeader


HOME_ACTIONS = (
    ActionItem("market", "查看市场行情", "当前报价、历史行情与行情回放", "1"),
    ActionItem("reference", "查找市场标的", "股票、期货、期权及交易市场", "2"),
    ActionItem("strategy", "配置并运行策略", "选择策略、填写参数并启动", "3"),
    ActionItem("resources", "管理运行资源", "账户、行情数据、模型与通知", "4"),
    ActionItem("research", "准备数据研究", "研究与回测所需的数据", "5"),
    ActionItem("operations", "维护系统", "工作区、后台进程与问题排查", "6"),
)


class HomeScreen(Screen[None]):
    TITLE = "Kairos"
    SUB_TITLE = "首页"
    BINDINGS = [
        Binding(str(index), f"open_{item.id}", item.label, show=False)
        for index, item in enumerate(HOME_ACTIONS, 1)
    ]

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Static("KAIROS  /  TRADING WORKBENCH", id="home-kicker")
        yield Label("交易，从这里开始", id="page-title")
        yield Static(self._workspace_text(), id="workspace-summary")
        yield ActionList(
            *HOME_ACTIONS,
            id="home-actions",
            classes="action-cards",
            spacious=True,
        )
        yield Footer()

    def _workspace_text(self) -> str:
        state = self.app.state  # type: ignore[attr-defined]
        root = state.project_root
        if root is None:
            return state.load_error or "未选择工作区"
        return f"{state.workspace_id}  ·  {root}"

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self.app.open_section(event.option.id)  # type: ignore[attr-defined]

    def _open(self, section: str) -> None:
        actions = self.query_one("#home-actions", ActionList)
        if actions.highlight_shortcut(section):
            actions.action_select()

    def action_open_market(self) -> None:
        self.app.open_section("market")  # type: ignore[attr-defined]

    def action_open_reference(self) -> None:
        self.app.open_section("reference")  # type: ignore[attr-defined]

    def action_open_strategy(self) -> None:
        self.app.open_section("strategy")  # type: ignore[attr-defined]

    def action_open_resources(self) -> None:
        self.app.open_section("resources")  # type: ignore[attr-defined]

    def action_open_research(self) -> None:
        self.app.open_section("research")  # type: ignore[attr-defined]

    def action_open_operations(self) -> None:
        self.app.open_section("operations")  # type: ignore[attr-defined]
