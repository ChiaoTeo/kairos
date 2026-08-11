from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import os
import sys
from typing import Mapping

from kairospy.application.workspace import Workspace
from kairospy.strategy import StrategyLogger

from ..services.bus import StrategyContextBus
from ..services.context import StrategyClientBundle
from ..services.host import StrategyHost
from ..services.journal import JsonlLifecycleJournal
from ..services.loader import StrategyEntrypoint, load_strategy
from ..services.rest import StrategyControlServer


@dataclass(frozen=True, slots=True)
class StrategyProcessComposition:
    """Fully assembled one-instance strategy process."""

    entrypoint: StrategyEntrypoint
    host: StrategyHost
    control: StrategyControlServer


def compose_strategy_process(
    workspace: Workspace,
    *,
    strategy_ref: str,
    launch_id: str,
    instance_id: str,
    mode: str = "paper",
    params: Mapping[str, object] | None = None,
) -> StrategyProcessComposition:
    # Contract facades own all strategy process-boundary adapters.  Concrete
    # wire decoding and Unix transport remain private implementation details.
    from kairospy.infrastructure.contracts.account import backtest_mark_to_market
    from kairospy.infrastructure.contracts.execution import backtest_market, intent_port
    from kairospy.infrastructure.contracts.market import (
        command_port,
        event_stream,
        snapshot_reader,
    )
    from kairospy.infrastructure.contracts.reference import client as reference_client

    entrypoint = load_strategy(
        strategy_ref, root=workspace.paths.project_root, params=params
    )
    instance = workspace.instance(mode, launch_id, instance_id)
    # The launch chooses whether Market is shared or instance-owned. Account
    # is always instance-owned regardless of the Market topology.
    market_scope = os.environ.get(
        "KAIROS_MARKET_SCOPE", "shared" if mode == "live" else "instance"
    )
    if market_scope not in {"shared", "instance"}:
        raise ValueError("KAIROS_MARKET_SCOPE must be shared or instance")
    market_runtime = None if market_scope == "shared" else instance
    market_socket = (
        workspace.paths.process_socket("market")
        if market_runtime is None
        else market_runtime.socket("market")
    )
    market_event_socket = (
        workspace.paths.process_socket("market-events")
        if market_runtime is None
        else market_runtime.socket("market-events")
    )
    market_snapshot = (
        workspace.paths.child("snapshots", "market", "market.snapshot")
        if market_runtime is None
        else market_runtime.snapshot("market", "market.snapshot")
    )
    market_client = command_port(market_socket, launch_id=launch_id)
    manifest_path = instance.component_manifest()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        execution_socket = manifest["components"]["execution"]["socket"]
        accounts = manifest.get("accounts", {})
        account_socket = None
        if isinstance(accounts, dict) and accounts:
            account_endpoint = accounts.get("main") or next(iter(accounts.values()))
            account_socket = account_endpoint["socket"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError(
            "strategy instance component endpoint manifest is incomplete"
        ) from error
    max_notional = None
    raw_max_notional = os.environ.get("KAIROS_LIVE_MAX_ORDER_NOTIONAL")
    if raw_max_notional:
        try:
            max_notional = Decimal(raw_max_notional)
        except InvalidOperation:
            max_notional = None
    execution_client = intent_port(
        execution_socket,
        allow_trading=mode != "live"
        or os.environ.get("KAIROS_LIVE_TRADING_ENABLED", "false") == "true",
        max_order_notional=max_notional,
        require_limit_orders=mode == "live"
        and os.environ.get("KAIROS_LIVE_REQUIRE_LIMIT_ORDERS", "true") == "true",
        launch_id=launch_id,
    )
    bus = StrategyContextBus(
        market=market_client,
        intents=execution_client,
    )
    snapshots = snapshot_reader(market_snapshot)
    stream = event_stream(market_event_socket, replayable=mode == "backtest")
    journal = JsonlLifecycleJournal(
        workspace.paths.child(
            "launches", mode, launch_id, "instances", instance_id, "lifecycle.jsonl"
        )
    )
    host = StrategyHost(
        entrypoint.strategy,
        launch_id=launch_id,
        instance_id=instance_id,
        clients=StrategyClientBundle(
            commands=bus,
            market_commands=market_client,
            execution_commands=execution_client,
            market_snapshots=snapshots,
            market_events=stream,
            reference=reference_client(
                snapshot_path=workspace.paths.reference_snapshot("catalog"),
                markets_snapshot_path=workspace.paths.reference_snapshot("markets"),
            ),
            backtest_market=(
                (lambda event: (backtest_market(execution_socket, event), None)[1])
                if mode == "backtest"
                else None
            ),
            backtest_account_mark=(
                (lambda event: backtest_mark_to_market(account_socket, event))
                if mode == "backtest" and account_socket is not None
                else None
            ),
        ),
        journal=journal,
        logger=StrategyLogger(
            fields={
                "launch_id": launch_id,
                "instance_id": instance_id,
                "strategy_id": entrypoint.strategy.strategy_id,
                "component": "strategy",
                "process_id": "strategy",
                "workspace_id": workspace.identity.workspace_id,
            },
            stream=sys.stdout,
        ),
    )
    control = StrategyControlServer(
        host,
        workspace.paths.launch_socket(mode, launch_id, instance_id),
    )
    return StrategyProcessComposition(entrypoint, host, control)
