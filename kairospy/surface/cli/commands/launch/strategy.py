"""Launch strategy control commands."""

from __future__ import annotations

from pathlib import Path

import typer

from kairospy.surface.cli.options import OutputFormat
from kairospy.system.apps.launch.application import LaunchControlApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication

from . import strategy_app
from .support import (
    _emit,
    _resolve_launch_target,
    _target,
)


@strategy_app.command("status")
def strategy_status(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    _emit(
        LaunchControlApplication(owner).status(
            _target(launch_id, resolved_instance, mode, workspace)
        ),
        output,
    )


@strategy_app.command("decision")
def strategy_decision(
    launch_id: str,
    strategy_decision_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Show one end-to-end Strategy decision trace."""

    owner = WorkspaceApplication().open(workspace)
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    target = _target(launch_id, resolved_instance, mode, workspace)
    _emit(
        LaunchControlApplication(owner).decision(target, strategy_decision_id),
        output,
    )


def _strategy_action(action: str):
    def command(
        launch_id: str,
        instance: str | None = typer.Option(None, "--instance"),
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        owner = WorkspaceApplication().open(workspace)
        resolved_instance, mode = _resolve_launch_target(
            owner, launch_id, None, instance
        )
        target = _target(launch_id, resolved_instance, mode, workspace)
        _emit(
            LaunchControlApplication(owner).strategy_control(target, action),
            output,
        )

    command.__name__ = f"strategy_{action}"
    return command


for _action in ("enable", "pause", "resume", "refresh"):
    strategy_app.command(_action)(_strategy_action(_action))
