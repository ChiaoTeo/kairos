from __future__ import annotations

import sys
import os
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from typing import Sequence, TextIO

import click
import typer
from typer.main import get_command

from .commands.launch import launch_app
from .commands.reference import reference_app
from .commands.account import account_passthrough
from .commands.integration import integration_passthrough
from .commands.market import market_passthrough
from .commands.order import order_passthrough
from .commands.root import (
    config_app,
    project_app,
    system_app,
    timeline_app,
)
from kairospy.application.workspace import WorkspaceApplication
from kairospy.application.system import ComponentProcessApplication
from kairospy.surface.console import ObserveApp
from kairospy.surface.console.data import SystemObserveReader
from kairospy.surface.console.models import recommended_action
from .options import OutputFormat, reset_command_output, set_command_output


app = typer.Typer(
    no_args_is_help=True,
    help="Build, run, and diagnose reproducible trading strategies.",
)
app.add_typer(
    launch_app,
    name="launch",
    help="Run strategies and inspect their status, logs, and reports.",
    rich_help_panel="Daily workflow",
)
app.add_typer(
    project_app,
    name="project",
    help="Create, scaffold, and diagnose a Kairos project.",
    rich_help_panel="Daily workflow",
)
app.add_typer(
    config_app,
    name="config",
    help="Inspect advanced workspace configuration.",
    rich_help_panel="Advanced tools",
)
app.command(
    "account",
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
    help="Configure accounts and inspect balances, positions, and orders.",
    rich_help_panel="Operations",
)(account_passthrough)
app.command(
    "integration",
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
    help="Inspect provider capabilities and run provider operations.",
    rich_help_panel="Operations",
)(integration_passthrough)
app.command(
    "market",
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
    help="Validate market data, manage subscriptions, and read snapshots.",
    rich_help_panel="Operations",
)(market_passthrough)
app.command(
    "order",
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
    help="Submit and inspect execution orders.",
    rich_help_panel="Operations",
)(order_passthrough)
app.add_typer(
    system_app,
    name="system",
    help="Diagnose and control workspace runtime components.",
    rich_help_panel="Operations",
)
app.add_typer(
    timeline_app,
    name="timeline",
    help="Inspect and export event timelines.",
    rich_help_panel="Advanced tools",
)
app.add_typer(
    reference_app,
    name="reference",
    help="Query reference assets, listings, and markets.",
    rich_help_panel="Operations",
)


def _cli_format(argv: Sequence[str]) -> str:
    """Resolve explicit output first, then the selected workspace manifest."""
    for index, item in enumerate(argv):
        if item in {"--output", "--format"} and index + 1 < len(argv):
            return argv[index + 1]
        for option in ("--output=", "--format="):
            if item.startswith(option):
                return item[len(option) :]
    # Project creation targets a workspace that does not exist yet. Its output
    # must not inherit the format of an unrelated workspace discovered from
    # the caller's current directory.
    if list(argv[:2]) == ["project", "init"]:
        return "text"
    workspace: str | None = None
    for index, item in enumerate(argv):
        if item == "--workspace" and index + 1 < len(argv):
            workspace = argv[index + 1]
        elif item.startswith("--workspace="):
            workspace = item.split("=", 1)[1]
    try:
        return WorkspaceApplication().resolve(workspace).cli_format
    except (FileNotFoundError, ValueError):
        return "json"


@app.command("observe", rich_help_panel="Daily workflow")
def observe(
    workspace: str | None = typer.Option(None, "--workspace"),
    refresh: float = typer.Option(
        2.0, "--refresh", min=0.2, help="Refresh interval in seconds"
    ),
    once: bool = typer.Option(
        False, "--once", help="Print one JSON observation and exit"
    ),
) -> None:
    """Open the project, launch, runtime, and market observation console."""
    value = WorkspaceApplication().resolve(workspace)
    reader = SystemObserveReader(ComponentProcessApplication(value), value.workspace_id)
    if once:
        import json

        snapshot = reader.read()
        typer.echo(
            json.dumps(
                {
                    "workspace_id": snapshot.workspace_id,
                    "components": snapshot.components,
                    "launches": snapshot.launches,
                    "market_snapshot": snapshot.market_snapshot,
                    "next_action": recommended_action(snapshot),
                },
                default=str,
            )
        )
        return
    ObserveApp(reader, refresh_seconds=refresh).run()


@app.command("tui", hidden=True)
def tui(workspace: str | None = typer.Option(None, "--workspace")) -> None:
    """Compatibility alias for ``observe``."""
    observe(workspace=workspace)


@app.command("browse", rich_help_panel="Advanced tools")
def browse(workspace: str | None = typer.Option(None, "--workspace")) -> None:
    """List workspace-owned files for low-level inspection."""
    value = WorkspaceApplication().resolve(workspace)
    for path in sorted(value.paths.root.rglob("*")):
        typer.echo(str(path.relative_to(value.paths.root)))


@app.command("version", rich_help_panel="Advanced tools")
def version() -> None:
    """Print the installed KairosPy version."""
    typer.echo("kairospy 0.1.0")


def execute_argv(argv: Sequence[str], stdout: TextIO) -> int:
    command = get_command(app)
    previous_format = os.environ.get("KAIROS_CLI_FORMAT")
    effective_format = OutputFormat(_cli_format(argv))
    render_token = set_command_output(effective_format)
    os.environ["KAIROS_CLI_FORMAT"] = effective_format.value
    try:
        with redirect_stdout(stdout), redirect_stderr(stdout):
            command.main(args=list(argv), prog_name="kairospy", standalone_mode=False)
    except click.ClickException as error:
        error.show(file=stdout)
        return error.exit_code
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 1
    except Exception as error:
        stdout.write(f"Error: {error}\n")
        return 1
    finally:
        reset_command_output(render_token)
        if previous_format is None:
            os.environ.pop("KAIROS_CLI_FORMAT", None)
        else:
            os.environ["KAIROS_CLI_FORMAT"] = previous_format
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return execute_argv(sys.argv[1:] if argv is None else argv, sys.stdout)
