from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Mapping

from kairospy.application.account.composition import (
    build_strategy_access as build_account_access,
)
from kairospy.application.agent.composition import (
    AgentProcessComposition,
    compose_agent,
)
from kairospy.application.agent.services.tools import AgentToolScope
from kairospy.application.capital.composition import (
    build_strategy_access as build_capital_access,
)
from kairospy.application.execution import ExecutionPolicy
from kairospy.application.execution.composition import (
    build_strategy_access as build_execution_access,
)
from kairospy.application.launch.application.connections import resolve_instance_connections
from kairospy.application.launch.application.strategy_runtime import (
    StrategyLaunchConfig,
)
from kairospy.application.launch.composition import build_backtest_driver
from kairospy.application.market.composition import (
    MarketAccessConfig,
    build_strategy_access as build_market_access,
)
from kairospy.application.notification.composition import (
    NotificationProcessComposition,
    compose_notifications,
)
from kairospy.application.portfolio.composition import (
    build_strategy_access as build_portfolio_access,
)
from kairospy.application.reference.composition import (
    build_strategy_access as build_reference_access,
)
from kairospy.application.risk.composition import (
    build_strategy_access as build_risk_access,
)
from kairospy.application.system.clients import InstanceSystemClients
from kairospy.application.workspace import Workspace
from kairospy.strategy import StrategyIdentity, StrategyLogger
from .services.decision_journal import StrategyDecisionJournal

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
    notifications: NotificationProcessComposition
    agent: AgentProcessComposition


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
        instance.normalized_config(),
        launch_id=launch_id,
        mode=mode,
    )
    connections = resolve_instance_connections(instance)
    clients = InstanceSystemClients.from_connections(connections)
    if (
        config.authoritative
        and config.execution_enabled
        and connections.execution is None
    ):
        raise RuntimeError(
            "Execution is enabled but the instance manifest has no connection"
        )
    if clients.market is None:
        raise RuntimeError("Market is enabled but the instance manifest has no connection")

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
        client=clients.market,
    )
    reference = build_reference_access(clients.reference)
    account = build_account_access(
        instance=instance,
        account_clients=clients.accounts,
        required_segments={
            account_id: connection.required_segments
            for account_id, connection in connections.accounts.items()
            if connection.required_segments
        },
    )
    portfolio = build_portfolio_access(
        launch_id=launch_id,
        mode=mode,
        account=account,
    )
    capital_enabled = bool(config.capital.get("enabled", False))
    if capital_enabled and connections.capital is None:
        raise RuntimeError("Capital is enabled but the instance manifest has no connection")
    capital = build_capital_access(
        identity=identity,
        capital_group_id=(
            str(config.capital["capital_group_id"]) if capital_enabled else None
        ),
        account_ids=tuple(connections.accounts),
        account_lease_fences={
            account_id: connection.lease_fence
            for account_id, connection in connections.accounts.items()
            if connection.lease_fence is not None
        },
        client=(clients.capital if capital_enabled else None),
    )
    agent = compose_agent(
        workspace=workspace,
        instance=instance,
        config=config.agent,
        tool_scope=AgentToolScope(
            workspace.identity.workspace_id,
            launch_id,
            instance_id,
            identity.strategy_id,
            tuple(str(account_id) for account_id in connections.accounts),
        ),
    )
    risk = build_risk_access(
        instance=instance,
        client=clients.risk,
        account_ids=tuple(connections.accounts),
        strategy_id=identity.strategy_id,
    )
    execution = build_execution_access(
        instance=instance,
        client=(clients.execution if config.execution_enabled else None),
        identity=identity,
        account_ids=tuple(connections.accounts),
        policy=ExecutionPolicy(
            allow_trading=config.allow_trading,
            max_order_notional=config.max_order_notional,
            require_limit_orders=config.require_limit_orders,
        ),
        decorate_commands=lambda commands: agent.decorate_commands(
            commands,
            launch_id=launch_id,
            account=account,
        ),
    )

    logger = StrategyLogger(
        fields={
            "launch_id": launch_id,
            "instance_id": instance_id,
            "strategy_id": entrypoint.strategy.strategy_id,
            "component": "strategy",
            "process_id": "strategy",
            "workspace_id": workspace.identity.workspace_id,
        },
        stream=sys.stdout,
    )
    notifications = compose_notifications(
        workspace=workspace,
        instance=instance,
        identity=identity,
        mode=mode,
        config=config.notifications,
        logger=logger,
    )
    if config.replay_start is not None:
        notifications.application.bind_event(config.replay_start)
    lifecycle_routes = config.notifications.get("lifecycle_routes", ())
    if not isinstance(lifecycle_routes, (list, tuple)):
        raise ValueError("notifications.lifecycle_routes must be an array")

    application = StrategyApplication(
        entrypoint.strategy,
        launch_id=launch_id,
        instance_id=instance_id,
        reference=reference,
        market=market.application,
        account=account,
        portfolio=portfolio,
        capital=capital,
        risk=risk,
        execution=execution,
        agent=agent.application,
        agent_events=None if agent.events is None else agent.events.events,
        agent_synchronize=agent.synchronize if mode == "backtest" else None,
        notifications=notifications.application,
        decision_journal=StrategyDecisionJournal(
            instance.artifact("strategy-decisions.jsonl")
        ),
        decision_notification_routes=tuple(str(route) for route in lifecycle_routes),
        journal=StrategyLifecycleJournal(instance.lifecycle_journal()),
        state_path=instance.state("strategy", "state.json"),
        backtest=build_backtest_driver(
            mode=mode,
            connections=connections,
            clients=clients,
            execution_enabled=config.execution_enabled,
        ),
        params=params,
        logger=logger,
        replay_end=config.replay_end,
    )
    return StrategyProcessComposition(
        entrypoint,
        application,
        StrategyControlServer(
            application,
            workspace.paths.launch_socket(mode, launch_id, instance_id),
        ),
        notifications,
        agent,
    )
