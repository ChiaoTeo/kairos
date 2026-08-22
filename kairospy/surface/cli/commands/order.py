"""Transparent shell for the canonical Rust Execution CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import typer

from kairospy.application.system import NativeCliApplication
from kairospy.application.workspace import WorkspaceApplication


HELP = """`kairos order` is the standalone order-facing entry for Execution.

The implementation owner is kairos-execution-cli; Order is not a separate
module CLI.

Standalone order evidence short paths read an explicit local evidence file and
do not connect to a running Execution server:
  kairos order audit --file execution-evidence.json
  kairos order inspect --file execution-evidence.json --order-id order-1
  kairos order journal --file execution-evidence.json --order-id order-1
  kairos order fills --file execution-evidence.json

Standalone order preview validates and normalizes a local order request without
submitting it:
  kairos order preview-submit --order-id order-1 --account-id main --instrument-id BTC-USDT --quantity 1 --execution-route-id route-1
  kairos order preview-cancel --order-id order-1 --reason "manual review"
  kairos order preview-replace --target-order-id order-1 --order-id order-2 --account-id main --instrument-id BTC-USDT --quantity 2 --execution-route-id route-1
  kairos order preview-submit-file --file submit-order.json
  kairos order preview-cancel-file --file cancel-order.json
  kairos order preview-replace-file --file replace-order.json

Current runtime execution actions and order facts are connected through scoped
component commands:
  kairos launch instance component execution ...
"""

CONNECTED_COMMANDS = {
    "cancel",
    "events",
    "fill",
    "history",
    "open",
    "open-orders",
    "orders",
    "reconcile",
    "reconcile-remote",
    "replace",
    "routes",
    "show",
    "snapshot",
    "status",
    "submit",
    "trace",
    "unknown-remote-orders",
}

REMOVED_COMMANDS = {
    "backtest": (
        "`kairos order backtest` has been removed. Backtests belong to the "
        "`kairos launch`, `kairos data`, and `kairos research` workflows."
    ),
    "link-unknown": (
        "`kairos order link-unknown` is not available. Unknown remote order "
        "linking requires an Execution runtime contract/API before it can be "
        "exposed through the launch component."
    ),
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


def order_passthrough(ctx: typer.Context) -> None:
    workspace, arguments = _workspace_and_arguments(ctx.args)
    if arguments and arguments[0] in {"standalone", "connected"}:
        explicit_mode = arguments[0]
        arguments = arguments[1:]
    else:
        explicit_mode = "standalone"
    if explicit_mode == "connected":
        raise typer.BadParameter(
            "`kairos order` runs standalone Execution order tools. Use "
            "`kairos launch instance component execution ...` for connected mode."
        )
    if arguments and arguments[0] in REMOVED_COMMANDS:
        raise typer.BadParameter(REMOVED_COMMANDS[arguments[0]])
    if arguments and arguments[0] in CONNECTED_COMMANDS:
        command = arguments[0]
        raise typer.BadParameter(
            f"`kairos order {command}` is a connected Execution runtime command. "
            "Use `kairos launch instance component execution ...` for the "
            "launch-scoped Execution server."
        )
    owner = WorkspaceApplication().resolve(workspace)
    result = NativeCliApplication(owner).invoke(
        "execution", [explicit_mode, *(arguments or ["--help"])]
    )
    output = result.stdout if result.returncode == 0 else result.stderr or result.stdout
    if output:
        typer.echo(output.rstrip(), nl=False)
    if result.returncode:
        raise typer.Exit(result.returncode)


__all__ = ["HELP", "order_passthrough"]
