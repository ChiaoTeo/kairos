"""Workspace-owned state shared by workbench screens."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kairospy.system.apps.observe.application import (
    ObserveSnapshot,
    SystemObserveApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication


@dataclass(slots=True)
class WorkbenchState:
    """Small cross-screen session state; business state remains in Applications."""

    owner: Any | None
    workspace_arg: Path | None
    snapshot: ObserveSnapshot | None = None
    load_error: str | None = None
    dry_run: bool = False
    no_exec: bool = False
    yes: bool = False

    @property
    def workspace_id(self) -> str:
        return (
            str(self.owner.workspace_id) if self.owner is not None else "未选择工作区"
        )

    @property
    def project_root(self) -> Path | None:
        if self.owner is None:
            return None
        return Path(self.owner.paths.project_root)

    def refresh_snapshot(self) -> ObserveSnapshot | None:
        """Refresh observable process facts without printing to the terminal."""

        if self.owner is None:
            self.snapshot = None
            return None
        try:
            previous = self.snapshot
            self.snapshot = SystemObserveApplication(self.owner).read()
            self.load_error = None
        except Exception as error:
            self.snapshot = previous
            self.load_error = str(error)
        return self.snapshot


def load_workbench_state(
    workspace: Path | None,
    *,
    dry_run: bool = False,
    no_exec: bool = False,
    yes: bool = False,
) -> WorkbenchState:
    """Resolve the workspace for Textual without emitting CLI presentation."""

    try:
        owner = WorkspaceApplication().resolve(workspace)
    except (FileNotFoundError, ValueError) as error:
        return WorkbenchState(
            owner=None,
            workspace_arg=workspace,
            load_error=str(error),
            dry_run=dry_run,
            no_exec=no_exec,
            yes=yes,
        )
    state = WorkbenchState(
        owner=owner,
        workspace_arg=workspace if workspace is not None else Path(owner.paths.root),
        dry_run=dry_run,
        no_exec=no_exec,
        yes=yes,
    )
    state.refresh_snapshot()
    return state
