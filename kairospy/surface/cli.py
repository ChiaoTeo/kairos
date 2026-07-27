from __future__ import annotations

import sys
from typing import Sequence, TextIO

import typer
from typer.testing import CliRunner

from kairospy.surface.products import broker_app, data_app, integrations_app, reference_app, run_app, strategy_app, streams_app


app = typer.Typer(no_args_is_help=True, help="KairosPy strategy runtime toolkit")
app.add_typer(data_app, name="data")
app.add_typer(streams_app, name="streams")
app.add_typer(integrations_app, name="integrations")
app.add_typer(reference_app, name="reference")
app.add_typer(broker_app, name="broker")
app.add_typer(strategy_app, name="strategy")
app.add_typer(run_app, name="run")


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        app()
        return 0
    return execute_argv(argv, sys.stdout)


def execute_argv(argv: Sequence[str], stdout: TextIO) -> int:
    result = CliRunner().invoke(app, list(argv), catch_exceptions=False)
    stdout.write(result.output)
    return int(result.exit_code)


__all__ = [
    "app",
    "execute_argv",
    "main",
]
