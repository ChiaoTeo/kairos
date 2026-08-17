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
from kairospy.domain_types import AccountId
from kairospy.application.workspace import InstanceWorkspace, Workspace
from kairospy.infrastructure.contracts.account import AccountContractClient
from kairospy.infrastructure.contracts.execution import (
    advance_time as advance_execution_time,
    backtest_market,
)
from kairospy.infrastructure.contracts.risk import RiskContractClient

from .application.endpoints import InstanceEndpoints
from .application.strategy_runtime import StrategyLaunchConfig
from kairospy.strategy import StrategyIdentity


@dataclass(frozen=True, slots=True)
class StrategyBacktestDriver:
    """Launch-owned concrete coordination for one backtest instance."""

    account_socket: Path | None
    account_snapshot: Path | None
    account_id: AccountId | None
    risk_socket: Path | None
    execution_socket: Path | None

    def advance_time(self, event_time_unix_nanos: int) -> None:
        if self.account_socket is not None:
            AccountContractClient(self.account_socket).advance_time(
                event_time_unix_nanos
            )
        if self.risk_socket is not None:
            RiskContractClient(self.risk_socket).advance_time(event_time_unix_nanos)
        if self.execution_socket is not None:
            advance_execution_time(self.execution_socket, event_time_unix_nanos)

    def apply_market(self, event: MarketEvent) -> ExecutionBacktestResult:
        if self.execution_socket is None:
            return ExecutionBacktestResult(())
        return map_execution_backtest_result(
            backtest_market(self.execution_socket, event)
        )

    def mark_account(self, event: MarketEvent) -> AccountSegmentSnapshot | None:
        if (
            self.account_socket is None
            or self.account_snapshot is None
            or self.account_id is None
        ):
            return None
        return mark_backtest_account(
            self.account_socket, self.account_snapshot, self.account_id, event
        )


def build_backtest_driver(
    *,
    mode: str,
    endpoints: InstanceEndpoints,
    execution_enabled: bool,
) -> StrategyBacktestDriver | None:
    if mode != "backtest":
        return None
    account_endpoint = next(iter(endpoints.accounts.values()), None)
    account_id = next(iter(endpoints.accounts), None)
    return StrategyBacktestDriver(
        account_socket=(None if account_endpoint is None else account_endpoint.socket),
        account_snapshot=(
            None if account_endpoint is None else account_endpoint.snapshot
        ),
        account_id=account_id,
        risk_socket=None if endpoints.risk is None else endpoints.risk.socket,
        execution_socket=(
            endpoints.execution.socket
            if execution_enabled and endpoints.execution is not None
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
        result = release_strategy_owner(
            workspace=workspace,
            instance=instance,
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
