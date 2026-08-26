"""Compact workspace and activity header."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from ..theme import RichThemeColors, rich_theme_colors

if TYPE_CHECKING:
    from ..app import KairosWorkbenchApp


class WorkspaceHeader(Horizontal):
    """Keep product identity, workspace, and current activity on one line."""

    def __init__(self) -> None:
        super().__init__(id="workspace-header")
        self._status_value = "就绪"

    def compose(self) -> ComposeResult:
        yield Static(id="workspace-title")
        with Horizontal(id="workspace-status"):
            yield Static(id="status-indicator")
            yield Static(id="command-status")
            yield Static(id="compact-command-status")

    def on_mount(self) -> None:
        self.app.theme_changed_signal.subscribe(self, self._theme_changed)
        self.refresh_project()
        self.set_status("就绪")

    def _theme_changed(self, _theme: object) -> None:
        self.refresh_project()
        self.set_status(self._status_value)

    def refresh_project(self) -> None:
        """Refresh the global project identity after opening or creating a project."""

        app = cast("KairosWorkbenchApp", self.app)
        state = app.state
        colors = rich_theme_colors(self.app.current_theme)
        title = Text("KAIROS", style=f"bold {colors.primary}")
        title.append("  ·  ", style=colors.muted)
        if state.owner is None:
            title.append("未打开项目", style=f"bold {colors.warning}")
        else:
            title.append(state.workspace_id)
        self.query_one("#workspace-title", Static).update(title)
        self.screen.title = f"Kairos · {state.workspace_id}"

    def set_status(self, value: str) -> None:
        """Render an activity value with a small semantic status marker."""

        self._status_value = value
        marker, style = _status_presentation(
            value, rich_theme_colors(self.app.current_theme)
        )
        self.query_one("#status-indicator", Static).update(Text(marker, style=style))
        self.query_one("#command-status", Static).update(Text(value, style=style))
        self.query_one("#compact-command-status", Static).update(
            Text(_compact_status(value), style=style)
        )


def _status_presentation(value: str, colors: RichThemeColors) -> tuple[str, str]:
    if "失败" in value or "错误" in value:
        return "×", colors.error
    if "正在" in value or "刷新中" in value:
        return "●", colors.primary
    if "完成" in value or "成功" in value:
        return "✓", colors.success
    if "取消" in value:
        return "■", colors.warning
    return "•", colors.muted


def _compact_status(value: str) -> str:
    """Keep the operational state readable when header width is constrained."""

    if value.startswith("已选择 "):
        count = value.removeprefix("已选择 ").partition(" 条")[0]
        return f"已选 {count} 条"
    return value.partition(" · ")[0]
