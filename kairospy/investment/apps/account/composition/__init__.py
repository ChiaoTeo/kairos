"""Private concrete construction for Strategy-facing Account access."""

from __future__ import annotations

from collections.abc import Mapping

from kairospy.system.apps.components.application.clients import AccountSystemClient
from kairospy.system.apps.workspace.application import InstanceWorkspace
from kairospy.primitives.account import AccountId
from kairospy.infrastructure.contracts.account import AccountClient

from ..application.application import AccountApplication
from ..application.models import AccountSegmentSnapshot


def build_strategy_access(
    *,
    instance: InstanceWorkspace,
    account_clients: Mapping[AccountId, AccountSystemClient],
    required_segments: Mapping[AccountId, tuple[str, ...]] | None = None,
) -> AccountApplication:
    """Build one current-view reader for each enabled logical Account."""

    if not account_clients:
        return AccountApplication({})
    return AccountApplication(
        {
            account_id: client.current_view(account_id)
            for account_id, client in account_clients.items()
        },
        AccountClient(
            next(iter(account_clients.values())).socket_path,
            account_id=str(next(iter(account_clients))),
            workspace_id=instance.workspace.workspace_id,
            aeron_dir=str(instance.workspace.paths.aeron_dir()),
        ).events,
        launch_id=instance.launch_id,
        instance_id=instance.instance_id,
        required_segments=required_segments,
    )


def mark_backtest_account(
    client: AccountSystemClient,
    account_id: AccountId,
    event: object,
) -> AccountSegmentSnapshot | None:
    """Apply one replay observation through Account and map its typed result."""

    result = client.mark_to_market_event(event)
    if result is None:
        return None
    segment_key = result.get("segment_key")
    if not isinstance(segment_key, str) or not segment_key.strip():
        raise ValueError("Account backtest result is missing segment_key")
    return (
        client.current_view(account_id)
        .snapshot()
        .segment(segment_key)
    )
