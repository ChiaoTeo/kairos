"""Transparent shell for the canonical Rust Market CLI."""

from __future__ import annotations

import typer

from kairospy.investment.apps.market.application.cli import (
    MarketCliApplication,
    parse_market_command_line,
)
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


def market_passthrough(ctx: typer.Context) -> None:
    try:
        command = parse_market_command_line(ctx.args)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    workspace, arguments = command.workspace, list(command.arguments)
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
    native_arguments = [explicit_mode, *(arguments or ["--help"])]
    if command.output is not None:
        native_arguments[:0] = ["--output", command.output]
    result = MarketCliApplication(owner).invoke(native_arguments)
    output = result.stdout if result.returncode == 0 else result.stderr or result.stdout
    if output:
        typer.echo(output.rstrip(), nl=False)
    if result.returncode:
        raise typer.Exit(result.returncode)


__all__ = ["HELP", "market_passthrough"]
