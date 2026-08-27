"""Private concrete construction for Strategy-facing Risk access."""

from __future__ import annotations

from kairospy.system.apps.components.application.clients import RiskSystemClient
from kairospy.system.apps.workspace.application import InstanceWorkspace
from kairospy.infrastructure.contracts.risk import RiskClient
from kairospy.primitives.account import AccountId

from ..application.application import RiskApplication


def build_strategy_access(
    *,
    instance: InstanceWorkspace,
    client: RiskSystemClient | None = None,
    account_ids: tuple[AccountId, ...],
    strategy_id: str,
) -> RiskApplication:
    """Build Risk latest-view access, or the module-owned unavailable behavior."""

    owner = (
        RiskClient(
            client.socket_path,
            actor_id=f"risk:{instance.instance_id}",
            workspace_id=instance.workspace.workspace_id,
            view_root=client.require_view_root(),
            launch_id=instance.launch_id,
            instance_id=instance.instance_id,
            aeron_dir=str(instance.workspace.paths.aeron_dir()),
            timeout=client.timeout,
        )
        if client is not None
        else None
    )
    current_view = None if owner is None else owner.current
    if owner is not None and current_view is None:
        raise RuntimeError("Risk owner client is missing its current-view capability")
    return RiskApplication(
        current_view,
        None if owner is None else owner.events,
        account_ids=account_ids,
        strategy_id=strategy_id,
        launch_id=instance.launch_id,
        instance_id=instance.instance_id,
    )
