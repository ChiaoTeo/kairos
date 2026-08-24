"""Transparent shell for the canonical Rust Market CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import typer

from kairospy.investment.apps.market.application.cli import MarketCliApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication


HELP = """Market standalone commands are owned by kairos-market-cli.

Canonical commands:
  validate, once, replay, download, datasets, reference-universe

Current runtime components are connected through scoped component commands:
  kairos system component market ...
  kairos launch instance component market ...

Component status, logs, restart, repair, dependents, subscriptions, and live
snapshots do not belong to `kairos market`.
"""

CONNECTED_COMMANDS = {
    "status",
    "snapshot",
    "refresh",
    "recover",
    "stop",
    "logs",
    "restart",
    "repair",
    "dependents",
    "subscribe",
    "unsubscribe",
    "pause-replay",
    "resume-replay",
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
            "`kairos market` runs standalone Market commands. Use "
            "`kairos system component market ...` or "
            "`kairos launch instance component market ...` for connected mode."
        )
    if arguments and arguments[0] in CONNECTED_COMMANDS:
        command = arguments[0]
        raise typer.BadParameter(
            f"`kairos market {command}` is a connected runtime command. "
            "Use `kairos system component market ...` for a workspace-scoped "
            "Market server or `kairos launch instance component market ...` "
            "for a launch-scoped Market server."
        )
    owner = WorkspaceApplication().resolve(workspace) if workspace is not None else None
    result = MarketCliApplication(owner).invoke(
        [explicit_mode, *(arguments or ["--help"])]
    )
    output = result.stdout if result.returncode == 0 else result.stderr or result.stdout
    if output:
        typer.echo(output.rstrip(), nl=False)
    if result.returncode:
        raise typer.Exit(result.returncode)


__all__ = ["HELP", "market_passthrough"]
