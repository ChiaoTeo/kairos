"""Private Launch composition shared by instance process adapters."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from kairospy.application.market.composition import release_strategy_owner
from kairospy.application.market import MarketEvent
from kairospy.application.account import AccountSegmentSnapshot
from kairospy.application.account.composition import mark_backtest_account
from kairospy.application.execution import ExecutionBacktestResult
from kairospy.application.execution.mapping import map_execution_backtest_result
from kairospy.application.system.clients import (
    AccountSystemClient,
    ExecutionSystemClient,
    InstanceSystemClients,
    RiskSystemClient,
    system_client,
)
from kairospy.primitives.account import AccountId
from kairospy.application.workspace import InstanceWorkspace, Workspace

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
            system_client("market", workspace.paths.process_socket("market"))
            if config.market_scope == "shared" or market_connection is None
            else system_client("market", market_connection.socket)
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
