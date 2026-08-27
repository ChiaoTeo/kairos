"""Private concrete construction for Strategy-facing Execution access."""

from __future__ import annotations

from collections.abc import Callable

from kairospy.system.apps.components.application.clients import ExecutionSystemClient
from kairospy.system.apps.workspace.application import InstanceWorkspace
from ..application.commands import ExecutionCommandClient
from kairospy.infrastructure.contracts.execution import ExecutionClient
from kairospy.primitives.account import AccountId
from kairospy.strategy import StrategyIdentity

from ..application.application import ExecutionApplication
from ..application.config import ExecutionPolicy
from ..services import ExecutionEventCursorCheckpoint


def build_strategy_access(
    *,
    instance: InstanceWorkspace,
    client: ExecutionSystemClient | None = None,
    identity: StrategyIdentity,
    policy: ExecutionPolicy,
    account_ids: tuple[AccountId, ...] = (),
    decorate_commands: Callable[[object], object] | None = None,
) -> ExecutionApplication:
    """Build Execution commands and current views for one Strategy identity."""

    cursor_checkpoint = ExecutionEventCursorCheckpoint(
        instance.state("strategy", "execution-event-cursor.json"),
        instance_id=identity.instance_id,
    )
    if client is None:
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
    owner = ExecutionClient(
        client.socket_path,
        workspace_id=instance.workspace.workspace_id,
        view_root=instance.snapshot(),
        launch_id=identity.launch_id,
        instance_id=identity.instance_id,
        aeron_dir=str(instance.workspace.paths.aeron_dir()),
        timeout=client.timeout,
    )
    commands = ExecutionCommandClient(
        owner.control,
        workspace_id=instance.workspace.workspace_id,
        allow_trading=policy.allow_trading,
        max_order_notional=policy.max_order_notional,
        require_limit_orders=policy.require_limit_orders,
        launch_id=identity.launch_id,
    )
    if decorate_commands is not None:
        commands = decorate_commands(commands)  # type: ignore[assignment]
    current_views = owner.current
    if current_views is None:
        raise RuntimeError("Execution owner client is missing its current-view capability")
    return ExecutionApplication(
        commands,
        current_views,
        owner.events,
        strategy_id=identity.strategy_id,
        instance_id=identity.instance_id,
        launch_id=identity.launch_id,
        account_ids=account_ids,
        cursor_checkpoint=cursor_checkpoint,
    )
