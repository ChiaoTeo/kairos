from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Mapping

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.account import AccountBalance, AccountContext, AccountSnapshot, AccountSource


def account_baseline_event(
    context: AccountContext,
    *,
    sequence: int,
    at: datetime,
    currency: str,
    equity: Decimal | str | int | float,
    source: AccountSource = AccountSource.SIMULATED,
    metadata: Mapping[str, object] | None = None,
) -> RuntimeEnvelope:
    value = Decimal(str(equity))
    snapshot = AccountSnapshot(
        context,
        balances=(AccountBalance.from_total_locked(currency, value, Decimal("0"), source=source),),
        observed_at=at,
        source=source,
        raw=dict(metadata or {}),
    )
    return RuntimeEnvelope("account", "baseline", at, sequence, snapshot)


__all__ = ["account_baseline_event"]
