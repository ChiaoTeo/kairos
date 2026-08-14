"""Private concrete construction for Strategy-facing Execution access."""

from __future__ import annotations

from pathlib import Path

from kairospy.application.workspace import InstanceWorkspace
from kairospy.application.system.binaries import resolve_binary
from kairospy.infrastructure.contracts.execution import ExecutionProjection
from kairospy.infrastructure.transport.commands import (
    ExecutionCommandClient,
    UnixJsonCommandClient,
)
from kairospy.infrastructure.transport.execution import AeronExecutionEventSource
from kairospy.domain_types import AccountId
from kairospy.strategy import StrategyIdentity

from .application import ExecutionApplication
from .config import ExecutionPolicy


def build_strategy_access(
    *,
    instance: InstanceWorkspace,
    endpoint: Path | None,
    identity: StrategyIdentity,
    policy: ExecutionPolicy,
    account_ids: tuple[AccountId, ...] = (),
) -> ExecutionApplication:
    """Build Execution commands and projections for one Strategy identity."""

    if endpoint is None:
        return ExecutionApplication(
            None,
            None,
            None,
            strategy_id=identity.strategy_id,
            instance_id=identity.instance_id,
            launch_id=identity.launch_id,
            account_ids=account_ids,
        )
    commands = ExecutionCommandClient(
        UnixJsonCommandClient(endpoint),
        allow_trading=policy.allow_trading,
        max_order_notional=policy.max_order_notional,
        require_limit_orders=policy.require_limit_orders,
        launch_id=identity.launch_id,
    )
    projection = ExecutionProjection(instance)
    return ExecutionApplication(
        commands,
        projection,
        AeronExecutionEventSource(
            aeron_dir=instance.paths.aeron_dir(),
            binary=resolve_binary("kairos-execution-event-bridge"),
        ),
        strategy_id=identity.strategy_id,
        instance_id=identity.instance_id,
        launch_id=identity.launch_id,
        account_ids=account_ids,
    )
