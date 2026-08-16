"""Private concrete construction for Strategy-facing Risk access."""

from __future__ import annotations

from pathlib import Path

from kairospy.application.workspace import InstanceWorkspace
from kairospy.application.system.binaries import resolve_binary
from kairospy.infrastructure.contracts.risk import RiskProjection, RiskViewKey
from kairospy.infrastructure.transport.risk import AeronRiskEventSource
from kairospy.domain_types import AccountId

from .application import RiskApplication


def build_strategy_access(
    *,
    instance: InstanceWorkspace,
    endpoint: Path | None,
    account_ids: tuple[AccountId, ...],
    strategy_id: str,
) -> RiskApplication:
    """Build Risk projection access, or the module-owned unavailable behavior."""

    enabled = endpoint is not None
    return RiskApplication(
        RiskProjection(
            instance.snapshot("risk", "risk.snapshot"),
            RiskViewKey(actor_id=f"risk:{instance.instance_id}"),
        )
        if enabled
        else None,
        AeronRiskEventSource(
            aeron_dir=instance.paths.aeron_dir(),
            binary=resolve_binary("kairos-risk-event-bridge"),
        )
        if enabled
        else None,
        account_ids=account_ids,
        strategy_id=strategy_id,
        launch_id=instance.launch_id,
        instance_id=instance.instance_id,
    )
