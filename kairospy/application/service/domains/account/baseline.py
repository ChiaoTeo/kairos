from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Mapping

from kairospy.core.account import (
    AccountBalance,
    AccountContext,
    AccountSnapshot,
    AccountSource,
)
from kairospy.application.runtime.model import RuntimeDataEnvelope, account_data_envelope


def account_baseline_event(
    context: AccountContext,
    *,
    sequence: int,
    at: datetime,
    currency: str,
    equity: Decimal | str | int | float,
    source: AccountSource = AccountSource.SIMULATED,
    metadata: Mapping[str, object] | None = None,
) -> RuntimeDataEnvelope:
    value = Decimal(str(equity))
    snapshot = AccountSnapshot(
        context,
        balances=(
            AccountBalance.from_total_locked(
                currency,
                value,
                Decimal("0"),
                source=source,
            ),
        ),
        observed_at=at,
        source=source,
    )
    return account_data_envelope(
        context,
        sequence=sequence,
        time=at,
        snapshot=snapshot,
        equity=value,
        source=source,
        metadata=metadata,
        stream=f"account.{context.environment.value}.{context.account.broker}.{context.account.account_id}",
    )


__all__ = ["account_baseline_event"]
