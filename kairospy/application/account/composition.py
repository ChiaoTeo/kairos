"""Private concrete construction for Strategy-facing Account access."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from kairospy.application.workspace import InstanceWorkspace
from kairospy.application.system.binaries import resolve_binary
from kairospy.domain_types import AccountId
from kairospy.infrastructure.contracts.account import AccountProjection
from kairospy.infrastructure.contracts.account import backtest_mark_to_market
from kairospy.infrastructure.transport.account import AeronAccountEventSource

from .application import AccountApplication
from .mapping import map_accounts_snapshot
from .models import AccountSegmentSnapshot


def build_strategy_access(
    *,
    instance: InstanceWorkspace,
    account_snapshots: Mapping[AccountId, Path],
) -> AccountApplication:
    """Build one projection reader for each enabled logical Account."""

    if not account_snapshots:
        return AccountApplication({})
    return AccountApplication(
        {
            account_id: AccountProjection(snapshot, account_id=account_id)
            for account_id, snapshot in account_snapshots.items()
        },
        AeronAccountEventSource(
            aeron_dir=instance.paths.aeron_dir(),
            binary=resolve_binary("kairos-account-event-bridge"),
        ),
        launch_id=instance.launch_id,
        instance_id=instance.instance_id,
    )


def mark_backtest_account(
    socket: Path,
    account_id: AccountId,
    event: object,
) -> AccountSegmentSnapshot | None:
    """Apply one replay observation through Account and map its typed result."""

    result = backtest_mark_to_market(socket, event)
    if result is None:
        return None
    segment_key = result.get("segment_key")
    if not isinstance(segment_key, str) or not segment_key.strip():
        raise ValueError("Account backtest result is missing segment_key")
    return (
        map_accounts_snapshot(result.get("snapshot"), enabled_account_ids=(account_id,))
        .account(account_id)
        .segment(segment_key)
    )
