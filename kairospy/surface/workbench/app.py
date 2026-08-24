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
from .transcript import WorkbenchTranscript, redact_text
from .widgets import WorkbenchLog


class KairosWorkbenchApp(App[int]):
    """One application shell for every guided Kairos workflow."""

    CSS_PATH = "styles/workbench.tcss"
    TITLE = "Kairos"
    ENABLE_COMMAND_PALETTE = True
    BINDINGS = [
        Binding("ctrl+q", "quit", "退出", show=False),
        Binding("question_mark", "help", "帮助"),
        Binding("ctrl+p", "command_palette", "命令", show=False),
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
            self.state.selected_launch = self.initial_launch_attach
            screen.call_after_refresh(
                screen.enter_launch_workflow,
                self.initial_launch_attach,
                "attach",
                None,
            )
        elif self.initial_launch_setup is not None:
            launch_id, source = self.initial_launch_setup
            self.state.selected_launch = launch_id
            screen.call_after_refresh(
                screen.enter_launch_workflow,
                launch_id,
                "setup",
                source,
            )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Record lifecycle facts; rendered results are captured by WorkbenchLog."""

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

    def action_cancel_operation(self) -> None:
        screen = self.screen
        if isinstance(screen, CommandLineScreen):
            screen.action_interrupt()

    def action_copy_page(self) -> None:
        """Copy every complete result log on the current page for agent handoff."""

        sections: list[str] = []
        logs = list(self.screen.query(WorkbenchLog))
        for log in logs:
            content = log.plain_text
            if not content:
                continue
            if len(logs) > 1 and log.id:
                sections.append(f"## {log.id}\n{content}")
            else:
                sections.append(content)
        page = redact_text("\n\n".join(sections)).strip()
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
            characters=len(page),
        )
        self.notify(
            f"已复制当前页面（{len(page)} 字符），可直接粘贴给 Agent",
            title="复制成功",
        )
