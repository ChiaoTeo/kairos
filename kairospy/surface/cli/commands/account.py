"""Transparent shell for the canonical Rust Account CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import typer

from kairospy.application.account.cli import AccountCliApplication
from kairospy.application.workspace import WorkspaceApplication


HELP = """Account standalone commands are owned by kairos-account-cli.

Canonical commands include:
  list, show, register, modify, simulate, schemas, schema, doctor
  credential-list, credential-create, credential-show, credential-delete
  snapshot, balances, positions, open-orders for local paper/simulated accounts

Current runtime account facts are connected through scoped component commands:
  kairos launch instance component account ...

"""

CONNECTED_COMMANDS = {
    "fill",
    "refresh",
    "reconcile",
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


def account_passthrough(ctx: typer.Context) -> None:
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
            "`kairos account` runs standalone Account commands. Use "
            "`kairos launch instance component account ...` for connected mode."
        )
    if arguments and arguments[0] in CONNECTED_COMMANDS:
        command = arguments[0]
        raise typer.BadParameter(
            f"`kairos account {command}` is a connected runtime command. "
            "Use `kairos launch instance component account ...` for a "
            "running launch-scoped Account component."
        )
    owner = WorkspaceApplication().resolve(workspace)
    result = AccountCliApplication(owner).invoke(
        [explicit_mode, *(arguments or ["--help"])]
    )
    output = result.stdout if result.returncode == 0 else result.stderr or result.stdout
    if output:
        typer.echo(output.rstrip(), nl=False)
    if result.returncode:
        raise typer.Exit(result.returncode)


__all__ = ["HELP", "account_passthrough"]
