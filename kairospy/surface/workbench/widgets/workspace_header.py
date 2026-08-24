"""Workspace-aware application header."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.widgets import Header

if TYPE_CHECKING:
    from ..app import KairosWorkbenchApp


class WorkspaceHeader(Header):
    """Keep product identity and the active workspace visible on every screen."""

    def __init__(self) -> None:
        super().__init__(icon="")

    def on_mount(self) -> None:
        app = cast("KairosWorkbenchApp", self.app)
        state = app.state
        self.screen.title = f"Kairos · {state.workspace_id}"
