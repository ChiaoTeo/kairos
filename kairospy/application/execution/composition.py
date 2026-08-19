"""Private concrete construction for Strategy-facing Execution access."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from kairospy.application.workspace import InstanceWorkspace
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
from .services import ExecutionEventCursorCheckpoint


def build_strategy_access(
    *,
    instance: InstanceWorkspace,
    endpoint: Path | None,
    identity: StrategyIdentity,
    policy: ExecutionPolicy,
    account_ids: tuple[AccountId, ...] = (),
    decorate_commands: Callable[[object], object] | None = None,
) -> ExecutionApplication:
    """Build Execution commands and projections for one Strategy identity."""

    cursor_checkpoint = ExecutionEventCursorCheckpoint(
        instance.state("strategy", "execution-event-cursor.json"),
        instance_id=identity.instance_id,
    )
    if endpoint is None:
        return ExecutionApplication(
            None,
            None,
            None,
            strategy_id=identity.strategy_id,
            instance_id=identity.instance_id,
            launch_id=identity.launch_id,
            account_ids=account_ids,
            cursor_checkpoint=cursor_checkpoint,
        )
    commands = ExecutionCommandClient(
        UnixJsonCommandClient(endpoint),
        allow_trading=policy.allow_trading,
        max_order_notional=policy.max_order_notional,
        require_limit_orders=policy.require_limit_orders,
        launch_id=identity.launch_id,
    )
    if decorate_commands is not None:
        commands = decorate_commands(commands)  # type: ignore[assignment]
    projection = ExecutionProjection(instance)
    return ExecutionApplication(
        commands,
        projection,
        AeronExecutionEventSource(
            aeron_dir=instance.workspace.paths.aeron_dir(),
        ),
        strategy_id=identity.strategy_id,
        instance_id=identity.instance_id,
        launch_id=identity.launch_id,
        account_ids=account_ids,
        cursor_checkpoint=cursor_checkpoint,
    )
