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

    enabled = client is not None
    return RiskApplication(
        client.latest_view(actor_id=f"risk:{instance.instance_id}")
        if enabled
        else None,
        RiskClient(
            client.socket_path,
            actor_id=f"risk:{instance.instance_id}",
            workspace_id=instance.workspace.workspace_id,
            aeron_dir=str(instance.workspace.paths.aeron_dir()),
        ).events
        if enabled
        else None,
        account_ids=account_ids,
        strategy_id=strategy_id,
        launch_id=instance.launch_id,
        instance_id=instance.instance_id,
    )
