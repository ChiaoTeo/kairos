"""Configuration profile commands."""

from __future__ import annotations

from pathlib import Path

import typer

from kairospy.surface.cli.commands.config import config_app, emit
from kairospy.surface.cli.options import OutputFormat
from kairospy.system.apps.configuration.application import ConfigApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication


profile_app = typer.Typer(no_args_is_help=True, help="Configuration profiles")
config_app.add_typer(profile_app, name="profile")


@profile_app.command("list")
def profile_list(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    emit(ConfigApplication(WorkspaceApplication().open(workspace)).profiles(), output)


@profile_app.command("create")
def profile_create(
    name: str,
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    emit(
        {
            "path": str(
                ConfigApplication(
                    WorkspaceApplication().open(workspace)
                ).create_profile(name)
            )
        },
        output,
    )


@profile_app.command("use")
def profile_use(
    name: str,
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    emit(
        {
            "path": str(
                ConfigApplication(WorkspaceApplication().open(workspace)).use_profile(
                    name
                )
            ),
            "profile": name,
        },
        output,
    )
