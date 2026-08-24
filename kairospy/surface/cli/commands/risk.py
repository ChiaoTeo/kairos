"""Transparent shell for standalone Risk CLI tools."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import typer

from kairospy.system.apps.components.application import NativeCliApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication


HELP = """`kairos risk` runs standalone Risk tools.

Canonical standalone commands:
  schema, doctor, preview

Runtime Risk facts and controls are connected through scoped component commands:
  kairos system component risk ...
  kairos launch instance component risk ...

Authorization against the running Risk owner uses connected pre-trade-check or
authorize-reserve. Standalone preview is a local dry-run and never reserves
runtime budget.
"""

CONNECTED_COMMANDS = {
    "advance-time",
    "authorize-reserve",
    "close-circuit",
    "consume",
    "health",
    "latest",
    "open-circuit",
    "publish-policy",
    "release",
    "resize",
    "status",
}


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
        result.append(item)
        index += 1
    if len(set(values)) > 1:
        raise typer.BadParameter("--workspace may be specified only once")
    return (Path(values[0]) if values else None), result


def risk_passthrough(ctx: typer.Context) -> None:
    workspace, arguments = _workspace_and_arguments(ctx.args)
    if not arguments or arguments == ["--help"] or arguments == ["-h"]:
        typer.echo(HELP.rstrip(), nl=False)
        return
    if arguments and arguments[0] in {"standalone", "connected"}:
        explicit_mode = arguments[0]
        arguments = arguments[1:]
    else:
        explicit_mode = "standalone"
    if explicit_mode == "connected":
        raise typer.BadParameter(
            "`kairos risk` runs standalone Risk tools. Use "
            "`kairos system component risk ...` or "
            "`kairos launch instance component risk ...` for connected mode."
        )
    if arguments and arguments[0] in CONNECTED_COMMANDS:
        command = arguments[0]
        raise typer.BadParameter(
            f"`kairos risk {command}` is a connected Risk runtime command. "
            "Use `kairos system component risk ...` for the workspace-scoped "
            "Risk server or `kairos launch instance component risk ...` for a "
            "launch-scoped Risk server."
        )
    owner = WorkspaceApplication().resolve(workspace)
    result = NativeCliApplication(owner).invoke(
        "risk", ["standalone", *(arguments or ["--help"])]
    )
    output = result.stdout if result.returncode == 0 else result.stderr or result.stdout
    if output:
        typer.echo(output.rstrip(), nl=False)
    if result.returncode:
        raise typer.Exit(result.returncode)


__all__ = ["HELP", "risk_passthrough"]
