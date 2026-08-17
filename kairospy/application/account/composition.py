"""Private concrete construction for Strategy-facing Account access."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from kairospy.application.workspace import InstanceWorkspace
from kairospy.domain_types import AccountId
from kairospy.infrastructure.contracts.account import AccountCurrentViewReader
from kairospy.infrastructure.contracts.account import backtest_mark_to_market
from kairospy.infrastructure.transport.account import AeronAccountEventSource

from .application import AccountApplication
from .models import AccountSegmentSnapshot


def build_strategy_access(
    *,
    instance: InstanceWorkspace,
    account_snapshots: Mapping[AccountId, Path],
    required_segments: Mapping[AccountId, tuple[str, ...]] | None = None,
) -> AccountApplication:
    """Build one projection reader for each enabled logical Account."""

    if not account_snapshots:
        return AccountApplication({})
    return AccountApplication(
        {
            account_id: AccountCurrentViewReader(snapshot, account_id=account_id)
            for account_id, snapshot in account_snapshots.items()
        },
        AeronAccountEventSource(
            aeron_dir=instance.workspace.paths.aeron_dir(),
        ),
        launch_id=instance.launch_id,
        instance_id=instance.instance_id,
        required_segments=required_segments,
    )


def mark_backtest_account(
    socket: Path,
    snapshot: Path,
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
        AccountCurrentViewReader(snapshot, account_id=account_id)
        .snapshot(account_id)
        .segment(segment_key)
    )
