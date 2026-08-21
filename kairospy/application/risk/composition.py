"""Private concrete construction for Strategy-facing Risk access."""

from __future__ import annotations

from kairospy.application.system.clients import RiskSystemClient
from kairospy.application.workspace import InstanceWorkspace
from kairospy.infrastructure.transport.risk import AeronRiskEventSource
from kairospy.domain_types import AccountId

from .application import RiskApplication


def build_strategy_access(
    *,
    instance: InstanceWorkspace,
    client: RiskSystemClient | None = None,
    account_ids: tuple[AccountId, ...],
    strategy_id: str,
) -> RiskApplication:
    """Build Risk projection access, or the module-owned unavailable behavior."""

    enabled = client is not None
    return RiskApplication(
        client.latest_projection(actor_id=f"risk:{instance.instance_id}")
        if enabled
        else None,
        AeronRiskEventSource(
            aeron_dir=instance.workspace.paths.aeron_dir(),
        )
        if enabled
        else None,
        account_ids=account_ids,
        strategy_id=strategy_id,
        launch_id=instance.launch_id,
        instance_id=instance.instance_id,
    )
