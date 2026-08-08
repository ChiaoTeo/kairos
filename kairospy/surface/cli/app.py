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
from .commands.reference import _HELP, catalog_passthrough
from .commands.account import HELP as ACCOUNT_HELP, account_passthrough
from .commands.market import HELP as MARKET_HELP, market_passthrough
from .commands.order import HELP as ORDER_HELP, order_passthrough
from .commands.root import (
    config_app,
    project_app,
    system_app,
    timeline_app,
)
from kairospy.application.workspace import WorkspaceApplication


app = typer.Typer(no_args_is_help=True, help="KairosPy strategy runtime toolkit")
app.add_typer(launch_app, name="launch")
app.add_typer(project_app, name="project")
app.add_typer(config_app, name="config")
app.command(
    "account",
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
    help=ACCOUNT_HELP,
)(account_passthrough)
app.command(
    "market",
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
    help=MARKET_HELP,
)(market_passthrough)
app.command(
    "catalog",
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
    help=_HELP,
)(catalog_passthrough)
app.command(
    "order",
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
    help=ORDER_HELP,
)(order_passthrough)
app.add_typer(system_app, name="system")
app.add_typer(timeline_app, name="timeline")


def _cli_format(argv: Sequence[str]) -> str:
    """Resolve explicit output first, then the selected workspace manifest."""
    for index, item in enumerate(argv):
        if item in {"--output", "--format"} and index + 1 < len(argv):
            return argv[index + 1]
        for option in ("--output=", "--format="):
            if item.startswith(option):
                return item[len(option):]
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


@app.command("shell")
def shell(workspace: str | None = typer.Option(None, "--workspace")) -> None:
    value = WorkspaceApplication().resolve(workspace)
    typer.echo(f"kairos shell session for {value.workspace_id}; use subcommands with --workspace {value.paths.root}")


@app.command("tui")
def tui(workspace: str | None = typer.Option(None, "--workspace")) -> None:
    value = WorkspaceApplication().resolve(workspace)
    typer.echo(f"kairos read-only TUI surface for {value.workspace_id}: {value.paths.root}")


@app.command("browse")
def browse(workspace: str | None = typer.Option(None, "--workspace")) -> None:
    value = WorkspaceApplication().resolve(workspace)
    for path in sorted(value.paths.root.rglob("*")):
        typer.echo(str(path.relative_to(value.paths.root)))


@app.command("version")
def version() -> None:
    typer.echo("kairospy 0.1.0")


def execute_argv(argv: Sequence[str], stdout: TextIO) -> int:
    command = get_command(app)
    previous_format = os.environ.get("KAIROS_CLI_FORMAT")
    os.environ["KAIROS_CLI_FORMAT"] = _cli_format(argv)
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
        if previous_format is None:
            os.environ.pop("KAIROS_CLI_FORMAT", None)
        else:
            os.environ["KAIROS_CLI_FORMAT"] = previous_format
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return execute_argv(sys.argv[1:] if argv is None else argv, sys.stdout)
