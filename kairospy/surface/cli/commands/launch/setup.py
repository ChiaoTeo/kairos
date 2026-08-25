"""Launch setup and draft commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from kairospy.surface.cli.options import OutputFormat
from kairospy.system.apps.launch.application import LaunchConfigurationApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication

from . import (
    draft_app,
    launch_app,
)
from .support import (
    _emit,
    _launch_config_path,
)


@launch_app.command("init", help="Create a Launch in the unified workbench.")
def init_launch(
    launch_id: str = typer.Argument("new-launch"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    del output
    owner = WorkspaceApplication().open(workspace)
    path = owner.paths.launch_config(launch_id)
    if path.exists():
        raise typer.BadParameter(f"launch config already exists: {path}")
    _open_launch_setup(owner, launch_id, None)


@launch_app.command("edit", help="Edit a Launch in the unified workbench.")
def edit_launch(
    launch_id: str = typer.Argument(..., help="Launch id or launch TOML path."),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    del output
    owner = WorkspaceApplication().open(workspace)
    candidate = Path(launch_id).expanduser()
    if candidate.is_file():
        source = candidate.resolve()
        resolved_id = source.stem
    else:
        application = LaunchConfigurationApplication()
        draft = application.draft_path(owner.paths.root, launch_id)
        source = draft if draft.is_file() else _launch_config_path(owner, launch_id)
        resolved_id = source.stem
    _open_launch_setup(owner, resolved_id, source)


@draft_app.command("list")
def list_launch_drafts(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(LaunchConfigurationApplication().list_drafts(owner.paths.root), output)


@draft_app.command("discard")
def discard_launch_draft(
    launch_id: str,
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    LaunchConfigurationApplication().discard_draft(owner.paths.root, launch_id)
    _emit({"launch_id": launch_id, "status": "discarded"}, output)


def _open_launch_setup(owner: Any, launch_id: str, source: Path | None) -> None:
    from kairospy.surface.workbench import (
        LaunchSetupDeepLink,
        WorkbenchLaunchRequest,
        run_workbench,
    )

    run_workbench(
        WorkbenchLaunchRequest(
            workspace=Path(owner.paths.root),
            launch_setup=LaunchSetupDeepLink(launch_id, source),
            require_workspace=True,
        )
    )
