"""Compact workspace and activity header."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

if TYPE_CHECKING:
    from ..app import KairosWorkbenchApp


class WorkspaceHeader(Horizontal):
    """Keep product identity, workspace, and current activity on one line."""

    def __init__(self) -> None:
        super().__init__(id="workspace-header")

    def compose(self) -> ComposeResult:
        yield Static(id="workspace-title")
        with Horizontal(id="workspace-status"):
            yield Static(id="status-indicator")
            yield Static(id="command-status")

    def on_mount(self) -> None:
        self.refresh_project()
        self.set_status("就绪")

    def refresh_project(self) -> None:
        """Refresh the global project identity after opening or creating a project."""

        app = cast("KairosWorkbenchApp", self.app)
        state = app.state
        title = Text("KAIROS", style="bold cyan")
        title.append("  /  ", style="dim")
        if state.owner is None:
            title.append("未打开项目", style="bold yellow")
        else:
            title.append(state.workspace_id, style="bold")
        self.query_one("#workspace-title", Static).update(title)
        self.screen.title = f"Kairos · {state.workspace_id}"

    def set_status(self, value: str) -> None:
        """Render an activity value with a small semantic status marker."""

        style = _status_style(value)
        self.query_one("#status-indicator", Static).update(Text("●", style=style))
        self.query_one("#command-status", Static).update(Text(value, style=style))


def _status_style(value: str) -> str:
    if "失败" in value or "错误" in value:
        return "red"
    if "正在" in value or "刷新中" in value:
        return "cyan"
    if "完成" in value or "成功" in value:
        return "green"
    if "取消" in value:
        return "yellow"
    return "dim"
