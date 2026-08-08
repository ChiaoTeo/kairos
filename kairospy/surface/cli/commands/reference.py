"""Transparent shell for the canonical Rust Reference CLI.

This module intentionally does not define the Reference command tree. Rust is
the single owner of catalog syntax; Python only translates historical aliases
and binds the workspace when the caller omitted it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import typer

from kairospy.application.reference import ReferenceCliApplication
from kairospy.application.workspace import WorkspaceApplication


_HELP = """Reference and catalog commands are owned by kairos-reference-cli.

Canonical commands include:
  status, snapshot, refresh, sync, publish
  assets, participants, markets, events, query, search, show

"""


def _without_option(argv: Sequence[str], option: str) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == option:
            index += 2
            continue
        if item.startswith(option + "="):
            index += 1
            continue
        result.append(item)
        index += 1
    return result


def _workspace_and_arguments(argv: Sequence[str]) -> tuple[Path | None, list[str]]:
    workspace_values = []
    for index, item in enumerate(argv):
        if item == "--workspace":
            if index + 1 >= len(argv):
                raise typer.BadParameter("--workspace requires a value")
            workspace_values.append(argv[index + 1])
        elif item.startswith("--workspace="):
            workspace_values.append(item.split("=", 1)[1])
    if len(set(workspace_values)) > 1:
        raise typer.BadParameter("--workspace may be specified only once")
    workspace = Path(workspace_values[0]) if workspace_values else None
    return workspace, _without_option(argv, "--workspace")


def catalog_passthrough(ctx: typer.Context) -> None:
    workspace, arguments = _workspace_and_arguments(ctx.args)
    arguments = arguments or ["--help"]
    owner = WorkspaceApplication().resolve(workspace)

    application = ReferenceCliApplication(owner)
    result = application.invoke(arguments)
    output = result.stdout if result.returncode == 0 else result.stderr or result.stdout
    if output:
        typer.echo(output.rstrip(), nl=False)
    if result.returncode:
        raise typer.Exit(result.returncode)


__all__ = ["_HELP", "catalog_passthrough"]
