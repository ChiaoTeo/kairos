"""Textual application shell for the Kairos workbench."""

from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.binding import Binding

from .dialogs import HelpDialog
from .screens import (
    CommandLineScreen,
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


class KairosWorkbenchApp(App[int]):
    """One application shell for every guided Kairos workflow."""

    CSS_PATH = "styles/workbench.tcss"
    TITLE = "Kairos"
    ENABLE_COMMAND_PALETTE = True
    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("ctrl+q", "quit", "退出", show=False),
        Binding("question_mark", "help", "帮助"),
        Binding("ctrl+p", "command_palette", "命令", show=False),
        Binding("ctrl+c", "cancel_operation", "取消操作", show=False),
    ]

    def __init__(
        self,
        state: WorkbenchState,
        *,
        initial_section: str | None = None,
        initial_launch_attach: str | None = None,
        initial_launch_setup: tuple[str, Path | None] | None = None,
        observe_refresh_seconds: float = 2.0,
        watch_css: bool = False,
    ) -> None:
        super().__init__(watch_css=watch_css)
        self.state = state
        self.initial_section = initial_section
        self.initial_launch_attach = initial_launch_attach
        self.initial_launch_setup = initial_launch_setup
        self.observe_refresh_seconds = observe_refresh_seconds

    def on_mount(self) -> None:
        self.push_screen(CommandLineScreen())
        if self.initial_section is not None:
            self._submit_initial_section(self.initial_section)
        if self.initial_launch_attach is not None:
            from .screens.strategy import LaunchAttachScreen

            self.state.selected_launch = self.initial_launch_attach
            self.push_screen(LaunchAttachScreen(self.initial_launch_attach))
        if self.initial_launch_setup is not None:
            from .screens.launch_setup import LaunchSetupScreen

            launch_id, source = self.initial_launch_setup
            self.state.selected_launch = launch_id
            self.push_screen(LaunchSetupScreen(launch_id, source))

    def _submit_initial_section(self, section: str) -> None:
        screen = self.screen
        if not isinstance(screen, CommandLineScreen):
            return
        command = {
            "observe": "observe",
            "market": "market",
        }.get(section)
        if command is None:
            self.open_section(section)
        else:
            screen.submit(command)

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
        screen = self.screen
        if isinstance(screen, CommandLineScreen):
            screen.submit("help")
            return
        bindings = tuple(
            binding
            for binding in getattr(screen, "BINDINGS", ())
            if isinstance(binding, Binding)
        )
        self.push_screen(
            HelpDialog(
                screen.sub_title or screen.title or "当前页面",
                bindings,
                show_product_map=isinstance(screen, HomeScreen),
            )
        )

    def action_cancel_operation(self) -> None:
        cancelled = self.screen.workers.cancel_node(self.screen)
        if cancelled:
            self.notify(f"已取消 {len(cancelled)} 个当前页面任务", title="操作已取消")
