"""Launch-instance Reference component commands."""

from __future__ import annotations

from pathlib import Path

import typer

from kairospy.surface.cli.options import OutputFormat
from kairospy.system.apps.launch.application import LaunchRuntimeApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication

from . import instance_component_reference_app
from .support import (
    _emit,
    _instance_reference_client,
    _resolve_launch_target,
)


@instance_component_reference_app.command("status")
def launch_instance_component_reference_status(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect the Reference component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    statuses = LaunchRuntimeApplication(owner).component_status(instance_workspace)
    if "reference" not in statuses:
        raise typer.BadParameter("launch instance has no connected reference component")
    _emit(
        {
            **statuses["reference"],
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_reference_app.command("health")
def launch_instance_component_reference_health(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read Reference health selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_reference_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.health().to_json_dict(),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_reference_app.command("catalog")
def launch_instance_component_reference_catalog(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Reference catalog selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_reference_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.catalog().to_json_dict(),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )
