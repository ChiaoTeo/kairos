"""Transparent shell for the canonical Rust Reference CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import typer

from kairospy.investment.apps.reference.application.cli import ReferenceCliApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication


HELP = """Reference standalone commands are owned by kairos-reference-cli.

Canonical commands include:
  snapshot, catalog, assets, exchanges, instruments, listings
  markets, events, query, search, show

Current runtime reference facts and controls are connected through scoped
component commands:
  kairos system component reference ...
  kairos launch instance component reference ...

Component health, providers, validate, refresh, pause/resume, stream,
options-coverage, and catalog mutation do not belong to `kairos reference`.
"""

CONNECTED_COMMANDS = {
    "status",
    "health",
    "providers",
    "doctor",
    "logs",
    "validate",
    "stream",
    "refresh",
    "sync",
    "publish",
    "pause",
    "resume",
    "coverage",
    "options-coverage",
    "options-add",
    "options-remove",
}

CATALOG_MUTATION_COMMANDS = {
    ("assets", "add"),
    ("instruments", "add"),
    ("listings", "add"),
    ("catalog", "assets", "add"),
    ("catalog", "instruments", "add"),
    ("catalog", "listings", "add"),
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


def _standalone_arguments(arguments: Sequence[str]) -> list[str]:
    if not arguments:
        return ["standalone", "--help"]
    command = arguments[0]
    rest = list(arguments[1:])
    if command == "markets":
        return ["standalone", "markets", "list", *rest]
    if command == "assets":
        if rest and rest[0] in {"list", "show"}:
            return ["standalone", "assets", *rest]
        return ["standalone", "assets", "list", *rest]
    if command in {"exchanges", "instruments", "listings"}:
        return ["standalone", "catalog", command, *rest]
    if command == "catalog" and not rest:
        return ["standalone", "snapshot"]
    return ["standalone", *arguments]


def reference_passthrough(ctx: typer.Context) -> None:
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
            "`kairos reference` runs standalone Reference catalog commands. "
            "Use `kairos system component reference ...` or "
            "`kairos launch instance component reference ...` for connected mode."
        )
    if arguments and arguments[0] in CONNECTED_COMMANDS:
        command = arguments[0]
        raise typer.BadParameter(
            f"`kairos reference {command}` is a connected runtime command. "
            "Use `kairos system component reference ...` for the workspace-scoped "
            "Reference server or `kairos launch instance component reference ...` "
            "for a launch-scoped Reference server."
        )
    for command in CATALOG_MUTATION_COMMANDS:
        if tuple(arguments[: len(command)]) == command:
            rendered = " ".join(command)
            raise typer.BadParameter(
                f"`kairos reference {rendered}` mutates the Reference catalog and "
                "is not a standalone catalog query. Use the scoped Reference "
                "component after the owner contract/API supports that mutation."
            )
    owner = WorkspaceApplication().resolve(workspace)
    result = ReferenceCliApplication(owner).invoke(_standalone_arguments(arguments))
    output = result.stdout if result.returncode == 0 else result.stderr or result.stdout
    if output:
        typer.echo(output.rstrip(), nl=False)
    if result.returncode:
        raise typer.Exit(result.returncode)


__all__ = ["HELP", "reference_passthrough"]
