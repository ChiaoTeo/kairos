"""Textual application shell for the Kairos workbench."""

from __future__ import annotations

from textual.app import App
from textual.binding import Binding

from .screens import (
    HomeScreen,
    MarketScreen,
    OperationsScreen,
    ReferenceScreen,
    ResearchScreen,
    ResourcesScreen,
    StrategyScreen,
)
from .screens.observe import ObserveScreen
from .state import WorkbenchState


WORKBENCH_CSS = """
Screen {
    layout: vertical;
    background: $background;
}

Header {
    dock: top;
}

Footer {
    dock: bottom;
}

#page-title {
    height: 3;
    padding: 1 2 0 2;
    text-style: bold;
}

#workspace-summary {
    height: 2;
    padding: 0 2;
    color: $text-muted;
}

ActionList {
    height: 1fr;
    margin: 0 1 1 1;
    padding: 0 1;
    border: round $surface-lighten-2;
}

ModalScreen {
    align: center middle;
    background: $background 65%;
}

.dialog {
    width: 72;
    max-width: 90%;
    height: auto;
    max-height: 80%;
    padding: 1 2;
    border: round $primary;
    background: $surface;
}

.dialog-title {
    height: 2;
    text-style: bold;
}

.dialog-message {
    height: auto;
    margin-bottom: 1;
}

.dialog-actions {
    height: 3;
    align-horizontal: right;
}

.dialog-actions Button {
    margin-left: 1;
}

#dialog-options {
    height: auto;
    max-height: 20;
}

#resource-form {
    height: 1fr;
    padding: 0 2 1 2;
}

#launch-form {
    height: 1fr;
    padding: 0 2 1 2;
}

#launch-form Label {
    margin-top: 1;
}

#launch-form-help, #launch-form-error {
    color: $text-muted;
}

#connected-fields, #execution-fields, #backtest-fields, #live-fields {
    height: auto;
}

#resource-form Label {
    margin-top: 1;
}

#resource-form-help, #resource-form-error, #observe-status {
    color: $text-muted;
}

.form-actions {
    height: 3;
    align-horizontal: right;
    margin-top: 1;
}

.form-actions Button {
    margin-left: 1;
}

#observe-summary, #observe-status {
    height: 2;
    padding: 0 2;
}

#observe-body {
    height: 1fr;
    padding: 0 1;
}

#observe-components-panel {
    width: 2fr;
    border: round $surface-lighten-2;
}

#observe-side {
    width: 1fr;
    margin-left: 1;
    border: round $surface-lighten-2;
}

.panel-title {
    height: 1;
    padding: 0 1;
    color: $primary;
    text-style: bold;
}
"""


class KairosWorkbenchApp(App[int]):
    """One application shell for every guided Kairos workflow."""

    CSS = WORKBENCH_CSS
    TITLE = "Kairos"
    ENABLE_COMMAND_PALETTE = True
    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("question_mark", "help", "帮助"),
        Binding("ctrl+c", "cancel_operation", "取消操作", show=False),
    ]

    def __init__(
        self,
        state: WorkbenchState,
        *,
        initial_section: str | None = None,
        observe_refresh_seconds: float = 2.0,
    ) -> None:
        super().__init__()
        self.state = state
        self.initial_section = initial_section
        self.observe_refresh_seconds = observe_refresh_seconds

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())
        if self.initial_section is not None:
            self.open_section(self.initial_section)

    def open_section(self, section: str) -> None:
        """Open an explicit product screen as it becomes available."""

        if section == "market":
            self.push_screen(MarketScreen())
            return
        if section == "reference":
            self.push_screen(ReferenceScreen())
            return
        if section == "strategy":
            self.push_screen(StrategyScreen())
            return
        if section == "resources":
            self.push_screen(ResourcesScreen())
            return
        if section == "research":
            self.push_screen(ResearchScreen())
            return
        if section == "operations":
            self.push_screen(OperationsScreen())
            return
        if section == "observe":
            self.push_screen(
                ObserveScreen(refresh_seconds=self.observe_refresh_seconds)
            )
            return
        self.notify(f"{section} 页面正在迁移", title="Kairos Workbench")

    def action_quit(self) -> None:
        self.exit(0)

    def action_help(self) -> None:
        self.notify("使用方向键选择，Enter 打开，Esc 返回。", title="当前页面帮助")

    def action_cancel_operation(self) -> None:
        cancelled = self.screen.workers.cancel_node(self.screen)
        if cancelled:
            self.notify(f"已取消 {len(cancelled)} 个当前页面任务", title="操作已取消")
