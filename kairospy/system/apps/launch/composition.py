"""Private Launch composition shared by instance process adapters."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from kairospy.investment.apps.market.composition import release_strategy_owner
from kairospy.investment.apps.market.application import MarketEvent
from kairospy.investment.apps.account.application import AccountSegmentSnapshot
from kairospy.investment.apps.account.composition import mark_backtest_account
from kairospy.investment.apps.execution.application import ExecutionBacktestResult
from kairospy.investment.apps.execution.application.mapping import map_execution_backtest_result
from kairospy.system.apps.components.application.clients import (
    AccountSystemClient,
    ExecutionSystemClient,
    InstanceSystemClients,
    MarketSystemClient,
    RiskSystemClient,
)
from kairospy.primitives.account import AccountId
from kairospy.system.apps.workspace.application import InstanceWorkspace, Workspace

from .application.connections import InstanceConnections, resolve_instance_connections
from .application.strategy_runtime import StrategyLaunchConfig
from kairospy.strategy import StrategyIdentity


@dataclass(frozen=True, slots=True)
class StrategyBacktestDriver:
    """Launch-owned concrete coordination for one backtest instance."""

    account_id: AccountId | None
    account_client: AccountSystemClient | None
    risk_client: RiskSystemClient | None
    execution_client: ExecutionSystemClient | None

    def advance_time(self, event_time_unix_nanos: int) -> None:
        if self.account_client is not None:
            self.account_client.advance_time(event_time_unix_nanos)
        if self.risk_client is not None:
            self.risk_client.advance_time(event_time_unix_nanos)
        if self.execution_client is not None:
            self.execution_client.advance_time(event_time_unix_nanos)

    def apply_market(self, event: MarketEvent) -> ExecutionBacktestResult:
        if self.execution_client is None:
            return ExecutionBacktestResult(())
        return map_execution_backtest_result(
            self.execution_client.backtest_market(event)
        )

    def mark_account(self, event: MarketEvent) -> AccountSegmentSnapshot | None:
        if self.account_id is None or self.account_client is None:
            return None
        return mark_backtest_account(self.account_client, self.account_id, event)


def build_backtest_driver(
    *,
    mode: str,
    connections: InstanceConnections,
    clients: InstanceSystemClients,
    execution_enabled: bool,
) -> StrategyBacktestDriver | None:
    if mode != "backtest":
        return None
    account_id = next(iter(connections.accounts), None)
    return StrategyBacktestDriver(
        account_id=account_id,
        account_client=None if account_id is None else clients.accounts.get(account_id),
        risk_client=clients.risk,
        execution_client=(
            clients.execution
            if execution_enabled and clients.execution is not None
            else None
        ),
    )


def compose_strategy_process(
    workspace: Workspace,
    *,
    strategy_ref: str,
    launch_id: str,
    instance_id: str,
    mode: str = "paper",
    params: Mapping[str, object] | None = None,
):
    """Resolve Launch-owned context, then construct the Strategy runtime."""

    import sys

    from kairospy.investment.apps.account.composition import build_strategy_access as build_account_access
    from kairospy.investment.apps.capital.composition import build_strategy_access as build_capital_access
    from kairospy.investment.apps.execution.application import ExecutionPolicy
    from kairospy.investment.apps.execution.composition import build_strategy_access as build_execution_access
    from kairospy.investment.apps.market.composition import (
        MarketAccessConfig,
        build_strategy_access as build_market_access,
    )
    from kairospy.investment.apps.portfolio.composition import build_strategy_access as build_portfolio_access
    from kairospy.investment.apps.reference.composition import build_strategy_access as build_reference_access
    from kairospy.investment.apps.risk.composition import build_strategy_access as build_risk_access
    from kairospy.investment.composition import compose_investment_application
    from kairospy.strategy import StrategyIdentity, StrategyLogger
    from kairospy.strategy.apps.agent.composition import compose_agent
    from kairospy.strategy.apps.agent.services.tools import AgentToolScope
    from kairospy.strategy.apps.notification.composition import compose_notifications
    from kairospy.strategy.apps.runtime.application import load_strategy
    from kairospy.strategy.composition import compose_strategy_application

    instance = workspace.instance(mode, launch_id, instance_id)
    config = StrategyLaunchConfig.load(
        instance.normalized_config(),
        launch_id=launch_id,
        mode=mode,
    )
    connections = resolve_instance_connections(instance)
    clients = InstanceSystemClients.from_connections(connections)
    if config.authoritative and config.execution_enabled and connections.execution is None:
        raise RuntimeError(
            "Execution is enabled but the instance manifest has no connection"
        )
    if clients.market is None:
        raise RuntimeError(
            "Market is enabled but the instance manifest has no connection"
        )

    entrypoint = load_strategy(
        strategy_ref,
        root=workspace.paths.project_root,
        params=params,
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
        raise RuntimeError(
            "Capital is enabled but the instance manifest has no connection"
        )
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
    investment = compose_investment_application(
        reference=reference,
        market=market.application,
        account=account,
        portfolio=portfolio,
        risk=risk,
        capital=capital,
        execution=execution,
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
    return compose_strategy_application(
        workspace=workspace,
        entrypoint=entrypoint,
        instance=instance,
        investment=investment,
        agent=agent,
        notifications=notifications,
        logger=logger,
        config=config,
        backtest=build_backtest_driver(
            mode=mode,
            connections=connections,
            clients=clients,
            execution_enabled=config.execution_enabled,
        ),
        params=params,
    )


def release_strategy_market_owner(
    workspace: Workspace,
    instance: InstanceWorkspace,
) -> dict[str, object] | None:
    """Launch-owned reconciliation for a Strategy process that may be dead."""

    strategy_id = _journal_strategy_id(instance.lifecycle_journal())
    if strategy_id is None:
        return None
    try:
        config = StrategyLaunchConfig.load(
            instance.normalized_config(),
            launch_id=instance.launch_id,
            mode=instance.mode,
        )
        try:
            connections = resolve_instance_connections(instance)
            market_connection = connections.market
        except RuntimeError:
            market_connection = None
        market_client = (
            MarketSystemClient(workspace.paths.process_socket("market"))
            if config.market_scope == "shared" or market_connection is None
            else MarketSystemClient(market_connection.socket)
        )
        result = release_strategy_owner(
            client=market_client,
            identity=StrategyIdentity(
                strategy_id,
                instance.launch_id,
                instance.instance_id,
            ),
            scope=config.market_scope,
        )
        return {
            "status": result.status,
            "request_id": result.request_id,
            "result": {
                "removed_subscription_ids": list(result.removed_subscription_ids)
            },
            "error": result.error,
        }
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        return {"status": "failed", "error": str(error)}


def _journal_strategy_id(path: Path) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        strategy_id = value.get("strategy_id") if isinstance(value, Mapping) else None
        if isinstance(strategy_id, str) and strategy_id.strip():
            return strategy_id
    return None
