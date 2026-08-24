"""Workspace-aware application header."""

from __future__ import annotations

from textual.widgets import Header


class WorkspaceHeader(Header):
    """Keep product identity and the active workspace visible on every screen."""

    def on_mount(self) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        self.screen.title = f"Kairos · {state.workspace_id}"
