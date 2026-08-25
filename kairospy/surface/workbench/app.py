"""Textual application shell for the Kairos workbench."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Any

from textual.app import App
from textual.binding import Binding
from textual.worker import Worker

from .screens import CommandLineScreen
from .state import WorkbenchState
from kairospy.surface.presentation import redact_text

from .transcript import WorkbenchTranscript
from .preferences import load_project_theme, save_project_theme
from .theme import (
    KAIROS_THEME,
    KAIROS_THEMES,
    resolve_theme_name,
)
from .widgets import ActivityStream


class KairosWorkbenchApp(App[int]):
    """One application shell for every guided Kairos workflow."""

    CSS_PATH = "styles/workbench.tcss"
    TITLE = "Kairos"
    ENABLE_COMMAND_PALETTE = True
    BINDINGS = [
        Binding("ctrl+q", "quit", "退出", show=False),
        Binding("question_mark", "help", "帮助"),
        Binding("ctrl+p", "command_palette", "命令", show=False),
        Binding("ctrl+t", "change_theme", "主题", show=False),
        Binding("ctrl+c", "cancel_operation", "取消操作", show=False),
        Binding("ctrl+shift+c", "copy_page", "复制当前页", show=False),
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
        transcript_path: Path | None = None,
    ) -> None:
        super().__init__(watch_css=watch_css)
        for theme in KAIROS_THEMES:
            self.register_theme(theme)
        self.theme = load_project_theme(state.owner) or KAIROS_THEME.name
        self.state = state
        self.transcript = WorkbenchTranscript.create(state, transcript_path)
        self.initial_section = initial_section
        self.initial_launch_attach = initial_launch_attach
        self.initial_launch_setup = initial_launch_setup
        self.observe_refresh_seconds = observe_refresh_seconds

    def run(self, **kwargs: Any) -> int | None:
        """Run without terminal mouse capture so native drag selection works."""

        kwargs.setdefault("mouse", False)
        return super().run(**kwargs)

    def on_mount(self) -> None:
        screen = CommandLineScreen()
        self.push_screen(screen)
        if self.initial_section is not None:
            self._submit_initial_section(self.initial_section)
        if self.initial_launch_attach is not None:
            screen.call_after_refresh(
                screen.enter_launch_workflow,
                self.initial_launch_attach,
                "attach",
                None,
            )
        elif self.initial_launch_setup is not None:
            launch_id, source = self.initial_launch_setup
            screen.call_after_refresh(
                screen.enter_launch_workflow,
                launch_id,
                "setup",
                source,
            )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Record lifecycle facts; rendered results are captured by ActivityStream."""

        self.transcript.record(
            "worker_state",
            screen=type(event.worker.node).__name__,
            worker=event.worker.name,
            state=event.state.name.lower(),
            error=(str(event.worker.error) if event.state.name == "ERROR" else None),
        )

    def _submit_initial_section(self, section: str) -> None:
        screen = self.screen
        if not isinstance(screen, CommandLineScreen):
            return
        screen.call_after_refresh(screen.enter_section, section)

    def open_section(self, section: str) -> None:
        """Enter a product context without adding another Screen."""

        self.transcript.record("navigation", section=section)
        screen = self.screen
        if isinstance(screen, CommandLineScreen):
            screen.enter_section(section)
        else:
            self.notify("Workbench 命令入口不可用", severity="error")

    def action_quit(self) -> None:
        self.transcript.record("session_finished", status="quit")
        self.exit(0)

    def action_help(self) -> None:
        screen = self.screen
        if isinstance(screen, CommandLineScreen):
            screen.submit("/help")

    def action_change_theme(self) -> None:
        """Open the curated theme picker in the shared interaction region."""

        screen = self.screen
        if isinstance(screen, CommandLineScreen):
            screen.present_theme_picker()

    def action_cancel_operation(self) -> None:
        screen = self.screen
        if isinstance(screen, CommandLineScreen):
            screen.action_interrupt()

    def action_copy_page(self) -> None:
        """Copy the current interaction and stable activity history for handoff."""

        self.copy_current_page()

    def select_theme(self, name: str) -> bool:
        """Select one curated Kairos theme by its short or registered name."""

        theme_name = resolve_theme_name(name)
        if theme_name is None:
            return False
        self.theme = theme_name
        save_project_theme(self.state.owner, theme_name)
        return True

    def copy_current_page(self, *, history_only: bool = False) -> None:
        """Copy a redacted page rendering, optionally limiting it to activities."""

        screen = self.screen
        if isinstance(screen, CommandLineScreen):
            page = screen.copy_page_text(history_only=history_only)
        else:
            page = "\n\n".join(
                log.plain_text for log in screen.query(ActivityStream) if log.plain_text
            )
        page = redact_text(page).strip()
        if not page:
            self.notify("当前页面没有可复制的结果", title="未复制", severity="warning")
            return
        self.copy_to_clipboard(page)
        if sys.platform == "darwin" and not self.is_headless:
            subprocess.run(
                ["pbcopy"],
                input=page,
                text=True,
                check=False,
                timeout=2,
            )
        self.transcript.record(
            "page_copied",
            screen=type(self.screen).__name__,
            history_only=history_only,
            characters=len(page),
        )
        self.notify(
            f"已复制当前页面（{len(page)} 字符），可直接粘贴给 Agent",
            title="复制成功",
        )
