from __future__ import annotations

from typing import Any

from kairospy.application.system.clients import CapitalSystemClient
from kairospy.domain_types import AccountId
from kairospy.strategy import StrategyIdentity

from .application import CapitalApplication


def build_strategy_access(
    *,
    identity: StrategyIdentity,
    capital_group_id: str | None,
    account_ids: tuple[AccountId, ...],
    account_lease_fences: dict[AccountId, str] | None = None,
    client: CapitalSystemClient | None = None,
    commands: Any | None = None,
    current_view: Any | None = None,
) -> CapitalApplication:
    """Build one Strategy facade; transport adapters are injected by composition."""

    if client is not None and commands is None:
        commands = client.control
    if (
        client is not None
        and current_view is None
        and capital_group_id is not None
    ):
        current_view = client.current_view(capital_group_id)
    if client is None:
        return CapitalApplication.disabled(
            strategy_id=identity.strategy_id,
            launch_id=identity.launch_id,
            instance_id=identity.instance_id,
            account_ids=account_ids,
        )
    return CapitalApplication(
        commands,
        current_view,
        strategy_id=identity.strategy_id,
        launch_id=identity.launch_id,
        instance_id=identity.instance_id,
        capital_group_id=capital_group_id,
        account_ids=account_ids,
        account_lease_fences=account_lease_fences,
    )
