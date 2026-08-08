from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import os
import sys
from typing import Mapping

from kairospy.application.workspace import Workspace
from kairospy.strategy import StrategyLogger
from kairospy.application.reference import ReferenceSnapshotClient

from ..services.bus import StrategyContextBus
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
    # Import transport adapters only when composing a process.  The transport
    # package also exposes market domain views, and importing it while the
    # strategy package is initializing would create a package-init cycle.
    from kairospy.infrastructure.transport import (
        ExecutionIntentCommandPort,
        MarketUnixCommandPort,
        MmapMarketSnapshotReader,
        UnixJsonCommandClient,
        UnixMarketEventStream,
    )

    entrypoint = load_strategy(strategy_ref, root=workspace.paths.project_root, params=params)
    instance = workspace.instance(mode, launch_id, instance_id)
    # The launch chooses whether Market is shared or instance-owned. Account
    # is always instance-owned regardless of the Market topology.
    market_scope = os.environ.get("KAIROS_MARKET_SCOPE", "shared" if mode == "live" else "instance")
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
    market_client = UnixJsonCommandClient(market_socket)
    manifest_path = instance.component_manifest()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        execution_socket = manifest["components"]["execution"]["socket"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("strategy instance component endpoint manifest is incomplete") from error
    execution_client = UnixJsonCommandClient(execution_socket)
    max_notional = None
    raw_max_notional = os.environ.get("KAIROS_LIVE_MAX_ORDER_NOTIONAL")
    if raw_max_notional:
        try:
            max_notional = Decimal(raw_max_notional)
        except InvalidOperation:
            max_notional = None
    bus = StrategyContextBus(
        market=MarketUnixCommandPort(market_client, launch_id=launch_id),
        intents=ExecutionIntentCommandPort(
            execution_client,
            allow_trading=mode != "live" or os.environ.get("KAIROS_LIVE_TRADING_ENABLED", "false") == "true",
            max_order_notional=max_notional,
            require_limit_orders=mode == "live" and os.environ.get("KAIROS_LIVE_REQUIRE_LIMIT_ORDERS", "true") == "true",
            launch_id=launch_id,
        ),
    )
    snapshots = MmapMarketSnapshotReader(market_snapshot)
    stream = UnixMarketEventStream(market_event_socket)
    journal = JsonlLifecycleJournal(
        workspace.paths.child("launches", mode, launch_id, "instances", instance_id, "lifecycle.jsonl")
    )
    host = StrategyHost(
        entrypoint.strategy,
        launch_id=launch_id,
        instance_id=instance_id,
        bus=bus,
        snapshots=snapshots,
        reference=ReferenceSnapshotClient(
            socket_path=workspace.paths.reference_socket(),
            snapshot_path=workspace.paths.reference_snapshot("catalog"),
            markets_snapshot_path=workspace.paths.reference_snapshot("markets"),
        ),
        stream=stream,
        journal=journal,
        logger=StrategyLogger(fields={
            "launch_id": launch_id,
            "instance_id": instance_id,
            "strategy_id": entrypoint.strategy.strategy_id,
            "component": "strategy",
        }, stream=sys.stdout),
    )
    control = StrategyControlServer(
        host,
        workspace.paths.launch_socket(mode, launch_id, instance_id),
    )
    return StrategyProcessComposition(entrypoint, host, control)
