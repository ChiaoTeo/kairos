from __future__ import annotations

from pathlib import Path
from typing import Any

from kairospy.domain_types import AccountId
from kairospy.infrastructure.contracts.capital import CapitalContractClient, CapitalProjection
from kairospy.strategy import StrategyIdentity

from .application import CapitalApplication


def build_strategy_access(
    *,
    identity: StrategyIdentity,
    capital_group_id: str | None,
    account_ids: tuple[AccountId, ...],
    account_lease_fences: dict[AccountId, str] | None = None,
    endpoint: Path | None = None,
    view_root: Path | None = None,
    commands: Any | None = None,
    projection: Any | None = None,
) -> CapitalApplication:
    """Build one Strategy facade; transport adapters are injected by composition."""

    if endpoint is not None and commands is None:
        commands = CapitalContractClient(endpoint)
    if (
        endpoint is not None
        and projection is None
        and view_root is not None
        and capital_group_id is not None
    ):
        projection = CapitalProjection(view_root, capital_group_id)
    if endpoint is None:
        return CapitalApplication.disabled(
            strategy_id=identity.strategy_id,
            launch_id=identity.launch_id,
            instance_id=identity.instance_id,
            account_ids=account_ids,
        )
    return CapitalApplication(
        commands,
        projection,
        strategy_id=identity.strategy_id,
        launch_id=identity.launch_id,
        instance_id=identity.instance_id,
        capital_group_id=capital_group_id,
        account_ids=account_ids,
        account_lease_fences=account_lease_fences,
    )
