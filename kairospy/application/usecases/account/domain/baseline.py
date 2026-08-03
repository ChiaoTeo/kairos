from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Mapping

from kairospy.domain.account import AccountBalance, AccountContext, AccountSnapshot, AccountSource


def account_baseline_snapshot(
    context: AccountContext,
    *,
    at: datetime,
    currency: str,
    equity: Decimal | str | int | float,
    source: AccountSource = AccountSource.SIMULATED,
    metadata: Mapping[str, object] | None = None,
) -> AccountSnapshot:
    value = Decimal(str(equity))
    snapshot = AccountSnapshot(
        context,
        balances=(AccountBalance.from_total_locked(currency, value, Decimal("0"), source=source),),
        observed_at=at,
        source=source,
        raw=dict(metadata or {}),
    )
    return snapshot


__all__ = ["account_baseline_snapshot"]
