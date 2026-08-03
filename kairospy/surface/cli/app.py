from __future__ import annotations

import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Sequence, TextIO

import click
import typer
from typer._click.exceptions import ClickException as TyperClickException
from typer.main import get_command

from kairospy.application.support.system.application.facade.context import ProjectNotFound, set_cli_context
from kairospy.application.support.system.application.diagnostics import record_exception
from kairospy.surface.cli.commands import (
    account_app,
    market_app,
    order_app,
    project_app,
    reference_app,
    launch_app,
    system_app,
)
from kairospy.surface.cli.options import OutputFormat, RootOptions


app = typer.Typer(no_args_is_help=True, help="KairosPy strategy runtime toolkit")
app.add_typer(project_app, name="project")
app.add_typer(launch_app, name="launch")
app.add_typer(account_app, name="account")
app.add_typer(order_app, name="order")
app.add_typer(market_app, name="market")
app.add_typer(reference_app, name="catalog")
app.add_typer(system_app, name="system")


@app.callback()
def main_options(
    ctx: typer.Context,
    cwd: Path | None = typer.Option(None, "-C", "--cwd", help="Launch as if Kairos was started in this directory."),
    profile: str | None = typer.Option(None, "--profile"),
    output: OutputFormat | None = typer.Option(None, "--output"),
    verbose: bool = typer.Option(False, "--verbose"),
    debug: bool = typer.Option(False, "--debug", help="Write diagnostic logs and show tracebacks for unexpected errors."),
) -> None:
    set_cli_context(cwd=cwd, profile=profile)
    ctx.obj = RootOptions(
        cwd=cwd,
        profile=profile,
        output=output,
        verbose=verbose,
        debug=debug,
    )


@app.command("shell", help="Open the stable interactive command shell.")
def shell(
    command: list[str] | None = typer.Option(None, "--command"),
) -> None:
    _shell(command)


@app.command("tui", hidden=True, help="Experimental Rich-rendered interactive shell.")
def tui() -> None:
    from kairospy.surface.interactive.tui import RichTui

    RichTui(command_executor=_execute_product_command).run()


def _run_shell(command: list[str] | None = None, *, surface_name: str = "shell") -> None:
    from kairospy.surface.interactive.tui import TextTui

    session = TextTui(
        command_executor=_execute_product_command,
        streaming_command_executor=_execute_product_command_streaming,
        surface_name=surface_name,
    )
    if command:
        for line in command:
            if session.handle(line):
                return
        return
    session.run()


def _shell(command: list[str] | None = None) -> None:
    _run_shell(command, surface_name="shell")


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


def _execute_product_command_streaming(argv: list[str], stdout: TextIO) -> int:
    return _invoke_app(argv, stdout)


def _invoke_app(argv: Sequence[str], stdout: TextIO) -> int:
    command = get_command(app)
    debug = "--debug" in argv
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
    except Exception as error:
        diagnostic = record_exception(error, operation="cli.command", command=" ".join(argv), context={"argv": list(argv)})
        stdout.write(f"Unexpected error: {error}\n")
        stdout.write(f"Diagnostic {diagnostic['diagnostic_id']}: {diagnostic['diagnostic_path']}\n")
        if debug:
            stdout.write(traceback.format_exc())
        else:
            stdout.write("Run with --debug for traceback details.\n")
        return 1
    return 0


__all__ = [
    "app",
    "execute_argv",
    "main",
]
