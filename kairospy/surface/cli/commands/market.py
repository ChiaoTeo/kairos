"""Transparent shell for the canonical Rust Market CLI."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from typing import Sequence, cast
from uuid import uuid4

import typer

from kairospy.application.market.cli import MarketCliApplication
from kairospy.application.system import ComponentProcessApplication, MarketSystemClient
from kairospy.application.workspace import Workspace, WorkspaceApplication
from kairospy.infrastructure.transport.market import MarketProjection
from kairospy.surface.cli.options import OutputFormat, render


HELP = """One-shot Market commands are owned by kairos-market-cli.

Canonical commands:
  validate, once, replay

Running-process commands:
  status, data-sources, snapshot, refresh, recover, stop, subscribe, unsubscribe

Snapshot views:
  snapshot quote --market-id market:binance:spot:BTCUSDT --source-id binance-spot
  snapshot bar --market-id market:binance:spot:BTCUSDT --source-id binance-spot --timeframe 1m
  snapshot greeks --market-id market:binance:options:BTC-260925-145000-C --source-id binance-options

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
    if arguments and arguments[0] in {
        "status",
        "data-sources",
        "snapshot",
        "refresh",
        "recover",
        "stop",
    }:
        _run_control_command(arguments, workspace)
        return
    if arguments and arguments[0] in {"subscribe", "unsubscribe"}:
        _run_subscription_command(arguments, workspace)
        return
    owner = WorkspaceApplication().resolve(workspace) if workspace is not None else None
    result = MarketCliApplication(owner).invoke(arguments or ["--help"])
    output = result.stdout if result.returncode == 0 else result.stderr or result.stdout
    if output:
        typer.echo(output.rstrip(), nl=False)
    if result.returncode:
        raise typer.Exit(result.returncode)


def _run_control_command(arguments: Sequence[str], workspace: Path | None) -> None:
    parser = argparse.ArgumentParser(prog=f"kairospy market {arguments[0]}")
    parser.add_argument(
        "--output",
        "--format",
        choices=[value.value for value in OutputFormat],
        default="json",
    )
    if arguments[0] == "snapshot":
        parser.add_argument(
            "kind",
            choices=["quote", "bar", "greeks"],
        )
        parser.add_argument("--market-id")
        parser.add_argument("--source-id", required=True)
        parser.add_argument("--symbol")
        parser.add_argument("--exchange", default="binance")
        parser.add_argument("--market-type", default="spot")
        parser.add_argument("--timeframe", default=None)
    elif arguments[0] == "data-sources":
        parser.add_argument("--market-id")
        parser.add_argument("--instrument-id")
        parser.add_argument("--observation-kind")
        parser.add_argument("--provider-id")
        parser.add_argument("--configured-only", action="store_true")
        parser.add_argument("--ready-only", action="store_true")
    parsed = parser.parse_args(list(arguments[1:]))
    owner = WorkspaceApplication().open(workspace)
    if arguments[0] == "snapshot":
        value = _read_mmap_snapshot(owner, parsed, parser)
        typer.echo(render(value, OutputFormat(parsed.output)))
        return
    client = _market_client(owner)
    if arguments[0] == "data-sources":
        query = "&".join(
            f"{key}={value}"
            for key, value in {
                "market_id": parsed.market_id,
                "instrument_id": parsed.instrument_id,
                "observation_kind": parsed.observation_kind,
                "provider_id": parsed.provider_id,
                "configured_only": "true" if parsed.configured_only else None,
                "ready_only": "true" if parsed.ready_only else None,
            }.items()
            if value is not None
        )
        value = client.data_sources(query)
    else:
        operation = getattr(client, arguments[0])
        value = operation()
    typer.echo(render(value, OutputFormat(parsed.output)))


def _read_mmap_snapshot(
    owner: Workspace, parsed: argparse.Namespace, parser: argparse.ArgumentParser
) -> dict[str, object]:
    market_id = parsed.market_id
    if market_id is None:
        if not parsed.symbol:
            parser.error("snapshot requires --market-id or --symbol")
        market_id = (
            f"market:{parsed.exchange.lower()}:{parsed.market_type.lower()}:"
            f"{parsed.symbol.upper()}"
        )
    projection = MarketProjection(
        owner.paths.child("snapshots", "market", "market-shared")
    )
    if parsed.kind == "quote":
        value = projection.read_quote(market_id, parsed.source_id)
    elif parsed.kind == "bar":
        if not parsed.timeframe:
            parser.error("snapshot bar requires --timeframe")
        value = projection.read_bar(market_id, parsed.source_id, parsed.timeframe)
    else:
        value = projection.read_greeks(market_id, parsed.source_id)
    return {
        "market_id": market_id,
        "source_id": parsed.source_id,
        "kind": parsed.kind,
        "status": "ready" if value is not None else "not_found",
        "value": None if value is None else asdict(value),
    }


def _run_subscription_command(arguments: Sequence[str], workspace: Path | None) -> None:
    parser = argparse.ArgumentParser(prog=f"kairospy market {arguments[0]}")
    parser.add_argument("--workspace", help=argparse.SUPPRESS)
    parser.add_argument(
        "--output",
        "--format",
        choices=[value.value for value in OutputFormat],
        default="json",
    )
    parser.add_argument("--subscription-id", required=True)
    if arguments[0] == "subscribe":
        parser.add_argument("--subject", required=True)
        parser.add_argument(
            "--source-id",
            help="Select one Market-owned data source when more than one route can satisfy the subscription",
        )
        parser.add_argument("--strategy-id", default="cli")
        parser.add_argument("--instance-id", default="cli")
        parser.add_argument("--selector", action="append", default=[])
        parser.add_argument("--exchange")
        parser.add_argument("--market-type")
        parser.add_argument("--asset-type")
        parser.add_argument("--identity")
        parser.add_argument("--param", action="append", default=[])
        parser.add_argument("--chain", action="store_true")
    parsed = parser.parse_args(list(arguments[1:]))
    owner = WorkspaceApplication().open(workspace)
    client = _market_client(owner)
    # The CLI's subscription id is the Market-owned runtime identity. Keep it
    # as the command id as well so the Rust process returns the same id and a
    # later `unsubscribe --subscription-id <value>` addresses that exact
    # subscription instead of an opaque UUID generated only by the adapter.
    command_id = parsed.subscription_id if arguments[0] == "subscribe" else str(uuid4())
    if arguments[0] == "subscribe":
        params: dict[str, object] = {}
        for item in parsed.param:
            if "=" not in item:
                parser.error("--param must use KEY=VALUE")
            key, value = item.split("=", 1)
            try:
                params[key] = json.loads(value)
            except json.JSONDecodeError:
                params[key] = value
        if parsed.chain:
            params["mode"] = "chain"
        value = client.subscribe(
            {
                "schema_version": 1,
                "command_id": command_id,
                "idempotency_key": parsed.subscription_id,
                "operation": "market.subscribe",
                "strategy_id": parsed.strategy_id,
                "instance_id": parsed.instance_id,
                "payload": {
                    "subject": parsed.subject,
                    "selectors": parsed.selector,
                    "source_id": parsed.source_id,
                    "exchange": parsed.exchange,
                    "market_type": parsed.market_type,
                    "asset_type": parsed.asset_type,
                    "identity": parsed.identity,
                    "params": params,
                    "dynamic": parsed.chain,
                },
            }
        )
    else:
        value = client.unsubscribe(
            {
                "schema_version": 1,
                "command_id": command_id,
                "idempotency_key": f"unsubscribe:{parsed.subscription_id}",
                "operation": "market.unsubscribe",
                "strategy_id": "cli",
                "instance_id": "cli",
                "payload": {"subscription_id": parsed.subscription_id},
            }
        )
    typer.echo(render(value, OutputFormat(parsed.output)))


def _market_client(owner: Workspace) -> MarketSystemClient:
    # A first subscription may open a provider connection and warm its
    # initial snapshot (notably Binance order books), which can exceed the
    # short health-check timeout used by generic system controls.
    processes = ComponentProcessApplication(owner, control_timeout=30.0)
    socket = owner.paths.process_socket("market")
    if socket.exists():
        return cast(MarketSystemClient, processes.client("market", socket))
    return cast(MarketSystemClient, processes.ensure_running("market"))


__all__ = ["HELP", "market_passthrough"]
