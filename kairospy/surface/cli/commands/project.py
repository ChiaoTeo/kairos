"""Project-scoped explicit CLI commands."""

from __future__ import annotations

from pathlib import Path

import typer

from kairospy.surface.cli.options import OutputFormat, render
from kairospy.system.apps.configuration.application import ConfigApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication


project_app = typer.Typer(no_args_is_help=True, help="Project commands")


def _emit(value: object, output: OutputFormat) -> None:
    typer.echo(render(value, output))


@project_app.command(
    "init", help="Create a Kairos project, optionally with a runnable starter."
)
def project_init(
    root: Path | None = typer.Argument(
        None, help="Project directory (prompted when omitted)"
    ),
    workspace_id: str | None = typer.Option(None, "--id"),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Do not prompt; require the project directory and --id",
    ),
    template: str | None = typer.Option(
        None,
        "--template",
        help="Install a runnable starter; currently supported: backtest.",
    ),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    del non_interactive
    template_name = template.strip().lower() if template is not None else None
    if root is None:
        raise typer.BadParameter(
            "project directory is required; use `kairos interactive` for guided setup"
        )
    root = Path(root)

    default_id = root.expanduser().resolve().name
    if workspace_id is None:
        raise typer.BadParameter(
            f"--id is required (suggested: {default_id}); "
            "use `kairos interactive` for guided setup"
        )

    try:
        workspace = WorkspaceApplication().init_project(
            root, workspace_id=workspace_id, template=template_name
        )
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="--template") from error
    next_steps = (
        [
            "cd " + str(workspace.paths.project_root),
            "kairos launch start demo-backtest",
            "kairos launch wait demo-backtest",
        ]
        if template_name == "backtest"
        else ["add a launch config under .kairos/config/launches"]
    )
    _emit(
        {
            "status": "initialized",
            "workspace_id": workspace.workspace_id,
            "root": str(workspace.paths.root),
            "template": template_name,
            "next_steps": next_steps,
        },
        output,
    )


@project_app.command("status", help="Show the resolved project and workspace paths.")
def project_status(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    value = WorkspaceApplication().open(workspace)
    _emit({"workspace_id": value.workspace_id, "root": str(value.paths.root)}, output)


@project_app.command("scaffold")
def project_scaffold(
    template: str = typer.Option(
        "backtest", "--template", help="Starter to install; currently: backtest."
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Install a runnable starter into an existing project."""

    owner = WorkspaceApplication().open(workspace)
    try:
        created = WorkspaceApplication().install_template(owner, template=template)
    except (ValueError, FileExistsError) as error:
        raise typer.BadParameter(str(error), param_hint="--template") from error
    _emit(
        {
            "status": "scaffolded",
            "template": template.strip().lower(),
            "created": [str(path) for path in created],
            "next_steps": [
                "kairos launch start demo-backtest",
                "kairos launch wait demo-backtest",
            ],
        },
        output,
    )


@project_app.command("doctor", help="Check project readiness and show the next action.")
def project_doctor(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).doctor(), output)


__all__ = ["project_app"]
