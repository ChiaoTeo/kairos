from __future__ import annotations

from typing import Any

from kairospy.system.apps.components.application.clients import CapitalSystemClient
from kairospy.infrastructure.contracts.capital import CapitalClient
from kairospy.primitives.account import AccountId
from kairospy.strategy import StrategyIdentity

from ..application.application import CapitalApplication


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

    if client is None:
        return CapitalApplication.disabled(
            strategy_id=identity.strategy_id,
            launch_id=identity.launch_id,
            instance_id=identity.instance_id,
            account_ids=account_ids,
        )
    if capital_group_id is None:
        raise ValueError("enabled Capital requires capital_group_id")
    if client.workspace_id is None:
        raise RuntimeError("Capital owner client requires workspace identity")
    route = client.event_route
    if route is None or route.scope != "instance":
        raise RuntimeError("Capital connection requires an explicit Instance event route")
    owner = CapitalClient(
        client.socket_path,
        capital_group_id=capital_group_id,
        workspace_id=client.workspace_id,
        view_root=client.require_view_root(),
        launch_id=identity.launch_id,
        instance_id=identity.instance_id,
        aeron_dir=str(route.aeron_dir),
        channel=route.channel,
        timeout=client.timeout,
    )
    if commands is None:
        commands = owner.control
    if current_view is None:
        current_view = owner.current
    if current_view is None:
        raise RuntimeError("Capital owner client is missing its current-view capability")
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
