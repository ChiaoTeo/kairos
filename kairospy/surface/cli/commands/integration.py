"""Transparent shell for the canonical Integration operations CLI."""

from __future__ import annotations

import typer

from kairospy.system.apps.integration.application import IntegrationCliApplication


HELP = """Provider operations are owned by kairos-integration-cli.

Canonical commands include transfer and earn.
"""


def integration_passthrough(ctx: typer.Context) -> None:
    if tuple(ctx.args) in {(), ("--help",), ("-h",)}:
        typer.echo(HELP.rstrip(), nl=False)
        return
    result = IntegrationCliApplication().invoke(ctx.args or ["--help"])
    output = result.stdout if result.returncode == 0 else result.stderr or result.stdout
    if output:
        typer.echo(output.rstrip(), nl=False)
    if result.returncode:
        raise typer.Exit(result.returncode)


__all__ = ["HELP", "integration_passthrough"]
