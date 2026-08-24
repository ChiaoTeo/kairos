"""Private concrete construction for Strategy-facing Account access."""

from __future__ import annotations

from collections.abc import Mapping

from kairospy.application.system.clients import AccountSystemClient
from kairospy.application.workspace import InstanceWorkspace
from kairospy.domain_types import AccountId
from kairospy.infrastructure.transport.account import AeronAccountEventSource

from .application import AccountApplication
from .models import AccountSegmentSnapshot


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
        AeronAccountEventSource(
            aeron_dir=instance.workspace.paths.aeron_dir(),
        ),
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
        .snapshot(account_id)
        .segment(segment_key)
    )
