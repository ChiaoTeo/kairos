"""Advanced workspace configuration command tree."""

from __future__ import annotations

from pathlib import Path

import typer

from kairospy.surface.cli.options import OutputFormat, render
from kairospy.surface.workbench import (
    WorkbenchLaunchRequest,
    WorkbenchWorkspaceError,
    run_workbench,
)


config_app = typer.Typer(no_args_is_help=True, help="Configuration commands")


def emit(value: object, output: OutputFormat) -> None:
    """Render one configuration command result through the CLI boundary."""

    typer.echo(render(value, output))


def open_resource_workbench(workspace: Path | None) -> None:
    """Open the resource section through the public Workbench launcher."""

    try:
        run_workbench(
            WorkbenchLaunchRequest(
                workspace=workspace,
                initial_section="resources",
                require_workspace=True,
            )
        )
    except WorkbenchWorkspaceError as error:
        raise typer.BadParameter(str(error)) from error


# Import command owners only after the group and scoped helpers exist.
from . import credentials as _credentials  # noqa: E402,F401
from . import data as _data  # noqa: E402,F401
from . import models as _models  # noqa: E402,F401
from . import profiles as _profiles  # noqa: E402,F401
from . import workspace as _workspace  # noqa: E402,F401


__all__ = ["config_app"]
