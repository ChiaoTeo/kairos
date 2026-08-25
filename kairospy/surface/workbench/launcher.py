"""Public process boundary for starting the unified Workbench."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .app import KairosWorkbenchApp
from .state import load_workbench_state


@dataclass(frozen=True, slots=True)
class LaunchSetupDeepLink:
    """Open one Launch configuration workflow after Workbench startup."""

    launch_id: str
    source: Path | None = None


@dataclass(frozen=True, slots=True)
class WorkbenchLaunchRequest:
    """Typed startup request shared by explicit CLI entry points."""

    workspace: Path | None = None
    initial_section: str | None = None
    launch_attach: str | None = None
    launch_setup: LaunchSetupDeepLink | None = None
    observe_refresh_seconds: float = 2.0
    dry_run: bool = False
    no_exec: bool = False
    yes: bool = False
    inline: bool = False
    transcript_path: Path | None = None
    require_workspace: bool = False

    def __post_init__(self) -> None:
        initial_targets = sum(
            value is not None
            for value in (self.initial_section, self.launch_attach, self.launch_setup)
        )
        if initial_targets > 1:
            raise ValueError("Workbench startup accepts only one initial target")


@dataclass(frozen=True, slots=True)
class WorkbenchRunResult:
    """Process-level facts returned without exposing Textual internals."""

    exit_code: int | None
    transcript_path: Path | None


class WorkbenchWorkspaceError(ValueError):
    """The requested Workbench entry requires a resolved workspace."""


def run_workbench(request: WorkbenchLaunchRequest) -> WorkbenchRunResult:
    """Resolve startup context and run the single Textual application."""

    state = load_workbench_state(
        request.workspace,
        dry_run=request.dry_run,
        no_exec=request.no_exec,
        yes=request.yes,
    )
    if request.require_workspace and state.owner is None:
        raise WorkbenchWorkspaceError(state.load_error or "当前没有可用的 workspace")
    setup = request.launch_setup
    workbench = KairosWorkbenchApp(
        state,
        initial_section=request.initial_section,
        initial_launch_attach=request.launch_attach,
        initial_launch_setup=(setup.launch_id, setup.source) if setup else None,
        observe_refresh_seconds=request.observe_refresh_seconds,
        watch_css=os.environ.get("KAIROS_TEXTUAL_DEV") == "1",
        transcript_path=request.transcript_path,
    )
    run_options = {"inline": True, "inline_no_clear": True} if request.inline else {}
    exit_code = workbench.run(**run_options)
    return WorkbenchRunResult(exit_code, workbench.transcript.path)


__all__ = [
    "LaunchSetupDeepLink",
    "WorkbenchLaunchRequest",
    "WorkbenchRunResult",
    "WorkbenchWorkspaceError",
    "run_workbench",
]
