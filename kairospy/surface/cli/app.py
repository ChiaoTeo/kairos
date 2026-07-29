from __future__ import annotations

import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Sequence, TextIO

import click
import typer
from typer._click.exceptions import ClickException as TyperClickException
from typer.main import get_command

from kairospy.application.system.facade.context import ProjectNotFound, set_cli_context
from kairospy.surface.cli.commands import (
    account_app,
    config_app,
    market_app,
    order_app,
    project_app,
    reference_app,
    run_app,
    strategy_app,
    timeline_app,
)
from kairospy.surface.cli.options import OutputFormat, RootOptions


app = typer.Typer(no_args_is_help=True, help="KairosPy strategy runtime toolkit")
app.add_typer(project_app, name="project")
app.add_typer(run_app, name="run")
app.add_typer(account_app, name="account")
app.add_typer(order_app, name="order")
app.add_typer(market_app, name="market")
app.add_typer(reference_app, name="reference")
app.add_typer(strategy_app, name="strategy")
app.add_typer(config_app, name="config")
app.add_typer(timeline_app, name="timeline")


@app.callback()
def main_options(
    ctx: typer.Context,
    cwd: Path | None = typer.Option(None, "-C", "--cwd", help="Run as if Kairos was started in this directory."),
    profile: str | None = typer.Option(None, "--profile"),
    output: OutputFormat | None = typer.Option(None, "--output"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    set_cli_context(cwd=cwd, profile=profile)
    ctx.obj = RootOptions(
        cwd=cwd,
        profile=profile,
        output=output,
        verbose=verbose,
    )


@app.command("shell")
def shell(
    command: list[str] | None = typer.Option(None, "--command"),
) -> None:
    _shell(command)


@app.command("app")
def app_command(
    command: list[str] | None = typer.Option(None, "--command"),
) -> None:
    _app(command)


@app.command("tui", hidden=True)
def tui() -> None:
    from kairospy.surface.interactive.tui import RichTui

    RichTui(command_executor=_execute_product_command).run()


def _app(command: list[str] | None = None) -> None:
    from kairospy.surface.interactive.tui import TextTui

    session = TextTui(command_executor=_execute_product_command)
    if command:
        for line in command:
            if session.handle(line):
                return
        return
    session.run()


def _shell(command: list[str] | None = None) -> None:
    _app(command)


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        if len(sys.argv) == 1 and sys.stdin.isatty() and sys.stdout.isatty():
            _shell()
            return 0
        return execute_argv(sys.argv[1:], sys.stdout)
    return execute_argv(argv, sys.stdout)


def execute_argv(argv: Sequence[str], stdout: TextIO) -> int:
    return _invoke_app(argv, stdout)


def _execute_product_command(argv: list[str]) -> tuple[int, str]:
    from io import StringIO

    output = StringIO()
    exit_code = _invoke_app(argv, output)
    return exit_code, output.getvalue()


def _invoke_app(argv: Sequence[str], stdout: TextIO) -> int:
    command = get_command(app)
    try:
        with redirect_stdout(stdout), redirect_stderr(stdout):
            command.main(args=list(argv), prog_name="kairospy", standalone_mode=False)
    except TyperClickException as error:
        error.show(file=stdout)
        return error.exit_code
    except click.ClickException as error:
        error.show(file=stdout)
        return error.exit_code
    except ProjectNotFound as error:
        stdout.write(str(error) + "\n")
        return 2
    except SystemExit as error:
        code = error.code
        return code if isinstance(code, int) else 1
    return 0


__all__ = [
    "app",
    "execute_argv",
    "main",
]
