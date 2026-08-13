from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Mapping

from kairospy.application.account.composition import (
    build_strategy_access as build_account_access,
)
from kairospy.application.execution import ExecutionPolicy
from kairospy.application.execution.composition import (
    build_strategy_access as build_execution_access,
)
from kairospy.application.launch.application.endpoints import resolve_instance_endpoints
from kairospy.application.launch.application.strategy_runtime import (
    StrategyLaunchConfig,
)
from kairospy.application.launch.composition import build_backtest_driver
from kairospy.application.market.composition import (
    MarketAccessConfig,
    build_strategy_access as build_market_access,
)
from kairospy.application.reference.composition import (
    build_strategy_access as build_reference_access,
)
from kairospy.application.risk.composition import (
    build_strategy_access as build_risk_access,
)
from kairospy.application.workspace import Workspace
from kairospy.strategy import StrategyIdentity, StrategyLogger

from .application.runtime import StrategyApplication
from .services.journal import StrategyLifecycleJournal
from .services.loader import StrategyEntrypoint, load_strategy
from .services.rest import StrategyControlServer


@dataclass(frozen=True, slots=True)
class StrategyProcessComposition:
    """Fully assembled one-instance Strategy process."""

    entrypoint: StrategyEntrypoint
    application: StrategyApplication
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
    """Select concrete module access and build one Strategy application."""

    entrypoint = load_strategy(
        strategy_ref, root=workspace.paths.project_root, params=params
    )
    instance = workspace.instance(mode, launch_id, instance_id)
    config = StrategyLaunchConfig.load(
        instance.root / "normalized-config.json",
        launch_id=launch_id,
        mode=mode,
    )
    endpoints = resolve_instance_endpoints(instance)
    if (
        config.authoritative
        and config.execution_enabled
        and endpoints.execution is None
    ):
        raise RuntimeError(
            "Execution is enabled but the instance manifest has no endpoint"
        )

    identity = StrategyIdentity(
        entrypoint.strategy.strategy_id,
        launch_id,
        instance_id,
    )
    market = build_market_access(
        workspace=workspace,
        instance=instance,
        identity=identity,
        config=MarketAccessConfig(
            scope=config.market_scope,
            replayable=mode == "backtest",
        ),
    )
    reference = build_reference_access(workspace)
    account_snapshots = {
        account_id: endpoint.snapshot
        for account_id, endpoint in endpoints.accounts.items()
        if endpoint.snapshot is not None
    }
    if len(account_snapshots) != len(endpoints.accounts):
        raise RuntimeError("Account endpoint manifest is missing a snapshot path")
    account = build_account_access(
        instance=instance,
        account_snapshots=account_snapshots,
    )
    risk = build_risk_access(
        instance=instance,
        endpoint=None if endpoints.risk is None else endpoints.risk.socket,
        account_ids=tuple(endpoints.accounts),
        strategy_id=identity.strategy_id,
    )
    execution = build_execution_access(
        instance=instance,
        endpoint=(
            None
            if not config.execution_enabled or endpoints.execution is None
            else endpoints.execution.socket
        ),
        identity=identity,
        account_ids=tuple(endpoints.accounts),
        policy=ExecutionPolicy(
            allow_trading=config.allow_trading,
            max_order_notional=config.max_order_notional,
            require_limit_orders=config.require_limit_orders,
        ),
    )

    application = StrategyApplication(
        entrypoint.strategy,
        launch_id=launch_id,
        instance_id=instance_id,
        reference=reference,
        market=market.application,
        account=account,
        risk=risk,
        execution=execution,
        journal=StrategyLifecycleJournal(instance.root / "lifecycle.jsonl"),
        state_path=instance.state("strategy", "state.json"),
        backtest=build_backtest_driver(
            mode=mode,
            endpoints=endpoints,
            execution_enabled=config.execution_enabled,
        ),
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
        replay_end=config.replay_end,
    )
    return StrategyProcessComposition(
        entrypoint,
        application,
        StrategyControlServer(
            application,
            workspace.paths.launch_socket(mode, launch_id, instance_id),
        ),
    )
