"""Workspace configuration inspection commands."""

from __future__ import annotations

from pathlib import Path

import typer

from kairospy.surface.cli.commands.config import config_app, emit
from kairospy.surface.cli.options import OutputFormat
from kairospy.system.apps.configuration.application import ConfigApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication


@config_app.command("paths")
def config_paths(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    emit(ConfigApplication(WorkspaceApplication().open(workspace)).paths(), output)


@config_app.command("manifest")
def config_manifest(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    emit(ConfigApplication(WorkspaceApplication().open(workspace)).manifest(), output)


@config_app.command("show")
def config_show(
    workspace: Path = typer.Option(None, "--workspace"),
    name: str | None = typer.Option(None, "--name"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    emit(ConfigApplication(WorkspaceApplication().open(workspace)).show(name), output)


@config_app.command("doctor")
def config_doctor(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    emit(ConfigApplication(WorkspaceApplication().open(workspace)).doctor(), output)


@config_app.command("explain")
def config_explain(
    name: str,
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    emit(
        ConfigApplication(WorkspaceApplication().open(workspace)).explain(name), output
    )


@config_app.command("operations")
def config_operations(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    emit(ConfigApplication(WorkspaceApplication().open(workspace)).operations(), output)


@config_app.command("status")
def config_status(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    emit(
        {"workspace_id": owner.workspace_id, "config": str(owner.paths.config)}, output
    )
