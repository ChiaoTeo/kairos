from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Iterable, Mapping

from kairospy.core.account import (
    AccountBalance,
    AccountContext,
    AccountSnapshot,
    AccountSource,
)

from .data import RuntimeDataEnvelope, account_data_envelope, system_data_envelope
from .line import RuntimeLine, RuntimeMode


def mode_runtime_line(
    mode: RuntimeMode | str,
    events: Iterable[RuntimeDataEnvelope],
    *,
    started_at: datetime | None = None,
    payload: Mapping[str, object] | None = None,
) -> RuntimeLine:
    values = tuple(events)
    runtime_mode = mode if isinstance(mode, RuntimeMode) else RuntimeMode(str(mode))
    if started_at is None and values:
        started_at = values[0].time
    prefix: tuple[RuntimeDataEnvelope, ...] = ()
    if started_at is not None:
        prefix = (
            system_data_envelope(
                f"runtime.mode.{runtime_mode.value}.started",
                sequence=1,
                time=started_at,
                payload={"mode": runtime_mode.value, **dict(payload or {})},
                stream="system.runtime",
            ),
        )
    return RuntimeLine(runtime_mode, (*prefix, *values))


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


__all__ = ["account_baseline_event", "mode_runtime_line"]
