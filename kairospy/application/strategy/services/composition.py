from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
from typing import Mapping

from kairospy.application.workspace import Workspace

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
    strategy_root: str | Path | None = None,
    params: Mapping[str, object] | None = None,
) -> StrategyProcessComposition:
    # Import transport adapters only when composing a process.  The transport
    # package also exposes market domain views, and importing it while the
    # strategy package is initializing would create a package-init cycle.
    from kairospy.infrastructure.transport import (
        AccountIntentCommandPort,
        MarketUnixCommandPort,
        MmapMarketSnapshotReader,
        UnixJsonCommandClient,
        UnixMarketEventStream,
    )

    root = workspace.paths.root if strategy_root is None else Path(strategy_root).expanduser().resolve()
    entrypoint = load_strategy(strategy_ref, root=root, params=params)
    market_client = UnixJsonCommandClient(workspace.paths.process_socket("market"))
    account_client = UnixJsonCommandClient(workspace.paths.process_socket("account"))
    max_notional = None
    raw_max_notional = os.environ.get("KAIROS_LIVE_MAX_ORDER_NOTIONAL")
    if raw_max_notional:
        try:
            max_notional = Decimal(raw_max_notional)
        except InvalidOperation:
            max_notional = None
    bus = StrategyContextBus(
        market=MarketUnixCommandPort(market_client),
        intents=AccountIntentCommandPort(
            account_client,
            allow_trading=mode != "live" or os.environ.get("KAIROS_LIVE_TRADING_ENABLED", "false") == "true",
            max_order_notional=max_notional,
            require_limit_orders=mode == "live" and os.environ.get("KAIROS_LIVE_REQUIRE_LIMIT_ORDERS", "true") == "true",
        ),
    )
    snapshots = MmapMarketSnapshotReader(workspace.paths.child("state", "market", "market.snapshot"))
    stream = UnixMarketEventStream(workspace.paths.process_socket("market-events"))
    journal = JsonlLifecycleJournal(
        workspace.paths.child("launches", mode, launch_id, "instances", instance_id, "lifecycle.jsonl")
    )
    host = StrategyHost(
        entrypoint.strategy,
        launch_id=launch_id,
        instance_id=instance_id,
        bus=bus,
        snapshots=snapshots,
        stream=stream,
        journal=journal,
    )
    control = StrategyControlServer(
        host,
        workspace.paths.launch_socket(mode, launch_id, instance_id),
    )
    return StrategyProcessComposition(entrypoint, host, control)
