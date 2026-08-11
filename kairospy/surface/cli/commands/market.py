"""Transparent shell for the canonical Rust Market CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence
from uuid import uuid4

import typer

from kairospy.application.market.cli import MarketCliApplication
from kairospy.application.system import ComponentProcessApplication
from kairospy.application.workspace import Workspace, WorkspaceApplication
from kairospy.surface.cli.options import OutputFormat, render


HELP = """One-shot Market commands are owned by kairos-market-cli.

Canonical commands:
  validate, once, replay

Running-process commands:
  status, snapshot, refresh, recover, stop, subscribe, unsubscribe

Snapshot views:
  snapshot quote --symbol BTCUSDT --exchange binance --market-type spot
  snapshot trade --symbol BTCUSDT --exchange binance --market-type spot
  snapshot orderbook --symbol BTCUSDT --exchange binance --market-type spot --depth 10
  snapshot bar --symbol BTCUSDT --exchange binance --market-type spot --timeframe 1m
  snapshot greeks --symbol BTC-260925-145000-C --exchange binance --market-type options
  snapshot chain --underlying SPY --exchange massive --market-type options

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
            nargs="?",
            choices=["all", "quote", "trade", "orderbook", "bar", "greeks", "chain"],
            default=None,
        )
        parser.add_argument("--symbol")
        parser.add_argument("--exchange", default="binance")
        parser.add_argument("--market-type", default="spot")
        parser.add_argument("--depth", type=int, default=10)
        parser.add_argument("--timeframe", default=None)
        parser.add_argument("--underlying")
    parsed = parser.parse_args(list(arguments[1:]))
    owner = WorkspaceApplication().open(workspace)
    client = _market_client(owner)
    operation = getattr(client, arguments[0])
    value = operation()
    if arguments[0] == "snapshot" and parsed.kind is not None:
        if parsed.depth <= 0:
            parser.error("--depth must be positive")
        value = _select_snapshot(
            value,
            kind=parsed.kind,
            symbol=parsed.symbol,
            exchange=parsed.exchange,
            market_type=parsed.market_type,
            depth=parsed.depth,
            timeframe=parsed.timeframe,
            underlying=parsed.underlying,
        )
    typer.echo(render(value, OutputFormat(parsed.output)))


def _select_snapshot(
    snapshot: dict[str, object],
    *,
    kind: str,
    symbol: str | None,
    exchange: str,
    market_type: str,
    depth: int,
    timeframe: str | None,
    underlying: str | None,
) -> dict[str, object]:
    if kind == "chain":
        if not underlying:
            raise typer.BadParameter("snapshot chain requires --underlying")
        target = f":{underlying.upper()}"
        market_ids: set[str] = set()
        subscriptions = snapshot.get("subscriptions", [])
        if isinstance(subscriptions, list):
            for subscription in subscriptions:
                if not isinstance(subscription, dict):
                    continue
                members = subscription.get("members", {})
                if not isinstance(members, dict):
                    continue
                for market_id, market in members.items():
                    if not isinstance(market_id, str) or not isinstance(market, dict):
                        continue
                    underlying_id = str(market.get("underlying_instrument_id") or "")
                    if underlying_id.upper().endswith(target):
                        market_ids.add(market_id)
        quotes: list[dict[str, object]] = []
        views = snapshot.get("views", {})
        if isinstance(views, dict):
            for view_key, observation in views.items():
                if not isinstance(view_key, str) or not view_key.endswith(".quote"):
                    continue
                if not isinstance(observation, dict):
                    continue
                item = next(iter(observation.values()), None)
                if isinstance(item, dict) and item.get("market_id") in market_ids:
                    quotes.append(item)
        quotes.sort(key=lambda item: str(item.get("market_id", "")))
        return {
            "underlying": underlying.upper(),
            "quotes": quotes,
            "count": len(quotes),
        }
    if not symbol:
        raise typer.BadParameter("snapshot business views require --symbol")
    market_id = f"market:{exchange.lower()}:{market_type.lower()}:{symbol.upper()}"
    result: dict[str, object] = {"market_id": market_id}

    if kind in {"all", "quote", "trade", "bar", "greeks"}:
        views = snapshot.get("views", {})
        if isinstance(views, dict):
            for view_key, observation in views.items():
                if not isinstance(view_key, str):
                    continue
                if not isinstance(observation, dict):
                    continue
                item = next(iter(observation.values()), None)
                if not isinstance(item, dict) or item.get("market_id") != market_id:
                    continue
                if view_key.endswith(".quote"):
                    item_kind = "quote"
                elif view_key.endswith(".trade"):
                    item_kind = "trade"
                elif view_key.endswith(".greek"):
                    item_kind = "greeks"
                elif ".bar" in view_key:
                    item_kind = "bar"
                    if timeframe and not view_key.endswith(f".bar.{timeframe}"):
                        continue
                else:
                    continue
                if kind in {"all", item_kind}:
                    result[item_kind] = item

    if kind in {"all", "orderbook"}:
        order_books = snapshot.get("order_books", {})
        if isinstance(order_books, dict):
            orderbook = order_books.get(market_id)
            if isinstance(orderbook, dict):
                result["orderbook"] = {
                    **orderbook,
                    "bids": list(orderbook.get("bids", []))[:depth],
                    "asks": list(orderbook.get("asks", []))[:depth],
                }

    if len(result) == 1:
        result["status"] = "not_found"
    return result


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


def _market_client(owner: Workspace):
    # A first subscription may open a provider connection and warm its
    # initial snapshot (notably Binance order books), which can exceed the
    # short health-check timeout used by generic system controls.
    processes = ComponentProcessApplication(owner, control_timeout=30.0)
    socket = owner.paths.process_socket("market")
    if socket.exists():
        return processes.client("market", socket)
    return processes.ensure_running("market")


__all__ = ["HELP", "market_passthrough"]
