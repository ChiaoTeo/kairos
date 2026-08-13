from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import os
import sys
from datetime import datetime
from typing import Mapping

from kairospy.application.workspace import Workspace
from kairospy.domain_types import AccountId
from kairospy.strategy import StrategyLogger

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
    # Strategy composition selects concrete process clients and projections.
    from kairospy.infrastructure.contracts.account import (
        AccountContractClient,
        AccountMmapProjection,
        backtest_mark_to_market,
    )
    from kairospy.infrastructure.contracts.execution import (
        ExecutionMmapProjection,
        advance_time as advance_execution_time,
        backtest_market,
    )
    from kairospy.infrastructure.contracts.risk import (
        RiskContractClient,
        RiskMmapProjection,
    )
    from kairospy.infrastructure.transport.commands import (
        ExecutionCommandClient,
        MarketCommandClient,
        UnixJsonCommandClient,
    )
    from kairospy.infrastructure.transport.market import (
        MmapMarketSnapshotReader,
        UnixMarketEventStream,
    )
    from kairospy.infrastructure.contracts.reference_client import ReferenceClient

    entrypoint = load_strategy(
        strategy_ref, root=workspace.paths.project_root, params=params
    )
    replay_end = None
    if mode == "backtest":
        raw_replay_end = os.environ.get("KAIROS_BACKTEST_END")
        if raw_replay_end:
            replay_end = datetime.fromisoformat(raw_replay_end.replace("Z", "+00:00"))
    instance = workspace.instance(mode, launch_id, instance_id)
    if mode == "backtest" and replay_end is None:
        # The normalized launch config is the authoritative replay boundary;
        # the environment variable remains an explicit override for ad-hoc
        # runs and tests.
        try:
            normalized = json.loads(
                (instance.root / "normalized-config.json").read_text(encoding="utf-8")
            )
            replay_end_value = (
                normalized.get("backtest", {}).get("market", {}).get("end")
            )
            if isinstance(replay_end_value, str):
                replay_end = datetime.fromisoformat(
                    replay_end_value.replace("Z", "+00:00")
                )
        except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
            pass
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
    market_client = MarketCommandClient(
        UnixJsonCommandClient(market_socket), launch_id=launch_id
    )
    manifest_path = instance.component_manifest()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        execution_endpoint = manifest.get("components", {}).get("execution")
        execution_socket = (
            execution_endpoint.get("socket")
            if isinstance(execution_endpoint, dict)
            else None
        )
        accounts = manifest.get("accounts", {})
        risk_endpoint = manifest.get("components", {}).get("risk")
        risk_socket = (
            risk_endpoint.get("socket") if isinstance(risk_endpoint, dict) else None
        )
        account_socket = None
        if isinstance(accounts, dict) and accounts:
            account_endpoint = accounts.get("main") or next(iter(accounts.values()))
            account_socket = account_endpoint["socket"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError(
            "strategy instance component endpoint manifest is incomplete"
        ) from error
    account_projection = AccountMmapProjection(
        instance.snapshot("account", "account.snapshot")
    )
    account_projections = {
        AccountId(str(account_id)): account_projection
        for account_id, value in accounts.items()
        if isinstance(value, dict) and value.get("socket")
    }
    max_notional = None
    raw_max_notional = os.environ.get("KAIROS_LIVE_MAX_ORDER_NOTIONAL")
    if raw_max_notional:
        try:
            max_notional = Decimal(raw_max_notional)
        except InvalidOperation:
            max_notional = None
    execution_client = (
        None
        if execution_socket is None
        else ExecutionCommandClient(
            UnixJsonCommandClient(execution_socket),
            allow_trading=mode != "live"
            or os.environ.get("KAIROS_LIVE_TRADING_ENABLED", "false") == "true",
            max_order_notional=max_notional,
            require_limit_orders=mode == "live"
            and os.environ.get("KAIROS_LIVE_REQUIRE_LIMIT_ORDERS", "true") == "true",
            launch_id=launch_id,
        )
    )
    snapshots = MmapMarketSnapshotReader(market_snapshot)
    stream = UnixMarketEventStream(market_event_socket, replayable=mode == "backtest")
    journal = JsonlLifecycleJournal(
        workspace.paths.child(
            "launches", mode, launch_id, "instances", instance_id, "lifecycle.jsonl"
        )
    )

    def advance_backtest_time(event_time_unix_nanos: int) -> None:
        if account_socket is not None:
            AccountContractClient(account_socket).advance_time(event_time_unix_nanos)
        if risk_socket is not None:
            RiskContractClient(risk_socket).advance_time(event_time_unix_nanos)
        if execution_socket is not None:
            advance_execution_time(execution_socket, event_time_unix_nanos)

    host = StrategyHost(
        entrypoint.strategy,
        launch_id=launch_id,
        instance_id=instance_id,
        clients=StrategyClientBundle(
            market_commands=market_client,
            execution_commands=execution_client,
            market_snapshots=snapshots,
            market_events=stream,
            reference_client=ReferenceClient(
                database_path=workspace.paths.reference_database(),
            ),
            account_projections=account_projections,
            execution_projection=(
                ExecutionMmapProjection(
                    instance.snapshot("execution", "execution.snapshot"),
                    instance.snapshot("intent", "intent.snapshot"),
                )
                if execution_socket is not None
                else None
            ),
            risk_projection=(
                RiskMmapProjection(instance.snapshot("risk", "risk.snapshot"))
                if risk_socket is not None
                else None
            ),
            history_root=workspace.paths.child("data", "market", "collections"),
            state_path=instance.state("strategy", "state.json"),
            backtest_market=(
                (lambda event: backtest_market(execution_socket, event))
                if mode == "backtest" and execution_socket is not None
                else None
            ),
            backtest_account_mark=(
                (lambda event: backtest_mark_to_market(account_socket, event))
                if mode == "backtest" and account_socket is not None
                else None
            ),
            backtest_time_advance=(
                advance_backtest_time if mode == "backtest" else None
            ),
        ),
        journal=journal,
        params=params,
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
        replay_end=replay_end,
        snapshot_views=()
        if strategy_ref == "builtin:interactive"
        else ("market.current",),
    )
    control = StrategyControlServer(
        host,
        workspace.paths.launch_socket(mode, launch_id, instance_id),
    )
    return StrategyProcessComposition(entrypoint, host, control)
