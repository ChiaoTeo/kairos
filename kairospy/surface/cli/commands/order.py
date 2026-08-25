"""Account-scoped standalone order commands owned by Execution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import typer

from kairospy.investment.apps.account.application.cli import AccountCliApplication
from kairospy.system.apps.components.application import NativeCliApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication


HELP = """`kairos order` opens a short-lived direct connection to the selected account's provider.

Every command requires an account context. Read operations use a read-capable
credential; submit/cancel/replace require a trade-capable credential. Accounts
with multiple trading segments also require `--segment`:
  kairos order open-orders --account-id main
  kairos order history --account-id main --symbol BTCUSDT
  kairos order order --account-id main --order-id 12345 --symbol BTCUSDT
  kairos order fills --account-id main --symbol BTCUSDT
  kairos order submit --account-id main --order-id cli-1 --instrument-id BTC-USDT --symbol BTCUSDT --quantity 1
  kairos order cancel --account-id main --order-id 12345 --symbol BTCUSDT
  kairos order replace --account-id main --target-order-id 12345 --order-id cli-2 --instrument-id BTC-USDT --symbol BTCUSDT --quantity 2

These commands do not connect to an Execution server. Runtime state, audit,
journal, trace, reconciliation and runtime order control are available only at
`kairos launch instance component execution ...`.
"""

DIRECT_COMMANDS = {
    "open-orders",
    "history",
    "order",
    "fills",
    "submit",
    "cancel",
    "replace",
}

WRITE_COMMANDS = {"submit", "cancel", "replace"}


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


def _take_option(arguments: list[str], name: str) -> tuple[str | None, list[str]]:
    values: list[str] = []
    result: list[str] = []
    index = 0
    prefix = f"{name}="
    while index < len(arguments):
        item = arguments[index]
        if item == name:
            if index + 1 >= len(arguments):
                raise typer.BadParameter(f"{name} requires a value")
            values.append(arguments[index + 1])
            index += 2
            continue
        if item.startswith(prefix):
            values.append(item[len(prefix) :])
            index += 1
            continue
        result.append(item)
        index += 1
    if len(set(values)) > 1:
        raise typer.BadParameter(f"{name} may be specified only once")
    return (values[0] if values else None), result


def _take_flag(arguments: list[str], name: str) -> tuple[bool, list[str]]:
    found = False
    result: list[str] = []
    for item in arguments:
        if item == name:
            found = True
        else:
            result.append(item)
    return found, result


def order_passthrough(ctx: typer.Context) -> None:
    workspace_path, arguments = _workspace_and_arguments(ctx.args)
    if not arguments or arguments in (["--help"], ["-h"]):
        typer.echo(HELP.rstrip(), nl=False)
        return
    if arguments[0] in {"standalone", "connected"}:
        raise typer.BadParameter(
            "`kairos order` is always standalone. Connected Execution commands live under "
            "`kairos launch instance component execution ...`."
        )
    command = arguments[0]
    if command not in DIRECT_COMMANDS:
        raise typer.BadParameter(
            f"unsupported standalone order command: {command}. Runtime diagnostics and "
            "control live under `kairos launch instance component execution ...`."
        )
    account_id, arguments = _take_option(arguments, "--account-id")
    segment, arguments = _take_option(arguments, "--segment")
    confirmed, arguments = _take_flag(arguments, "--yes")
    if not account_id:
        raise typer.BadParameter("standalone order command requires --account-id")

    owner = WorkspaceApplication().resolve(workspace_path)
    binding_args = [
        "standalone",
        "trading-binding",
        "--account-id",
        account_id,
        "--access",
        "trade" if command in WRITE_COMMANDS else "read",
    ]
    if segment:
        binding_args.extend(("--segment", segment))
    try:
        binding = AccountCliApplication(owner).run(binding_args)
    except RuntimeError as error:
        raise typer.BadParameter(str(error)) from error

    if (
        command in WRITE_COMMANDS
        and str(binding.get("environment", "")).lower() == "live"
    ):
        typer.echo(
            f"目标：account={account_id} · provider={binding.get('provider', 'unknown')} · "
            "environment=live · scope=direct-provider"
        )
        if not confirmed:
            raise typer.BadParameter(
                "live provider write requires explicit --yes; "
                "use `kairos interactive` for guided confirmation"
            )

    result = NativeCliApplication(owner).invoke(
        "execution",
        [
            "standalone",
            "--binding-json",
            json.dumps(binding),
            *(["--confirm-live"] if command in WRITE_COMMANDS else []),
            *arguments,
        ],
    )
    output = result.stdout if result.returncode == 0 else result.stderr or result.stdout
    if output:
        typer.echo(output.rstrip(), nl=False)
    if result.returncode:
        raise typer.Exit(result.returncode)


__all__ = ["HELP", "order_passthrough"]
