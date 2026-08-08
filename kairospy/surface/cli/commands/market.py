"""Transparent shell for the canonical Rust Market CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import typer

from kairospy.application.market.cli import MarketCliApplication
from kairospy.application.workspace import WorkspaceApplication


HELP = """One-shot Market commands are owned by kairos-market-cli.

Canonical commands:
  validate, once, replay

Running-process controls belong to `kairos system`; workspace data commands
belong to the workspace/data API.
"""


def _workspace_and_arguments(argv: Sequence[str]) -> tuple[Path | None, list[str]]:
    values: list[str] = []
    result: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--workspace":
            if index + 1 >= len(argv):
                raise typer.BadParameter("--workspace requires a value")
            values.append(argv[index + 1])
            index += 2
            continue
        if item.startswith("--workspace="):
            values.append(item.split("=", 1)[1])
            index += 1
            continue
        if item == "--format":
            result.append("--output")
        elif item.startswith("--format="):
            result.append("--output=" + item.split("=", 1)[1])
        else:
            result.append(item)
        index += 1
    if len(set(values)) > 1:
        raise typer.BadParameter("--workspace may be specified only once")
    return (Path(values[0]) if values else None), result


def market_passthrough(ctx: typer.Context) -> None:
    workspace, arguments = _workspace_and_arguments(ctx.args)
    owner = WorkspaceApplication().resolve(workspace) if workspace is not None else None
    result = MarketCliApplication(owner).invoke(arguments or ["--help"])
    output = result.stdout if result.returncode == 0 else result.stderr or result.stdout
    if output:
        typer.echo(output.rstrip(), nl=False)
    if result.returncode:
        raise typer.Exit(result.returncode)


__all__ = ["HELP", "market_passthrough"]
