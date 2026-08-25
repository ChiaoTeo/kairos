"""Launch-instance timeline commands."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from kairospy.surface.cli.options import OutputFormat
from kairospy.system.apps.launch.application import (
    LaunchInstanceTimelineApplication,
    LaunchRegistryApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication

from . import instance_timeline_app
from .support import _emit


def _timeline_instance(owner, launch_id: str, instance_id: str):
    entries = [
        entry
        for entry in LaunchRegistryApplication(owner).instances(launch_id)
        if entry.get("instance_id") == instance_id
    ]
    if not entries:
        raise typer.BadParameter(
            f"launch instance is not registered: {launch_id}/{instance_id}"
        )
    if len(entries) > 1:
        raise typer.BadParameter(
            f"launch instance {launch_id}/{instance_id} exists in multiple modes"
        )
    mode = str(entries[0].get("mode") or "")
    if not mode:
        raise typer.BadParameter("registered launch instance has no mode")
    return owner.instance(mode, launch_id, instance_id)


@instance_timeline_app.command("list")
def launch_instance_timeline_list(
    launch_id: str = typer.Argument(..., help="Launch id."),
    instance_id: str = typer.Argument(..., help="Launch instance id."),
    limit: int | None = typer.Option(None, "--limit"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    try:
        instance = _timeline_instance(owner, launch_id, instance_id)
        records = LaunchInstanceTimelineApplication(instance).list(limit=limit)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        raise typer.BadParameter(str(error)) from error
    _emit(records, output)


@instance_timeline_app.command("export")
def launch_instance_timeline_export(
    launch_id: str = typer.Argument(..., help="Launch id."),
    instance_id: str = typer.Argument(..., help="Launch instance id."),
    destination: Path = typer.Option(
        ..., "--destination", "--output-file", help="Export file path."
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    try:
        instance = _timeline_instance(owner, launch_id, instance_id)
        exported = LaunchInstanceTimelineApplication(instance).export(destination)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        raise typer.BadParameter(str(error)) from error
    _emit(
        {
            "launch_id": launch_id,
            "mode": instance.mode,
            "instance_id": instance_id,
            "destination": str(exported),
        },
        output,
    )
