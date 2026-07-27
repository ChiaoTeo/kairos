from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Iterable, Mapping

from kairospy.accounts import (
    AccountBalance,
    AccountContext,
    AccountSnapshot,
    AccountSource,
)

from .events import AccountRuntimeEvent, RuntimeEvent, SystemRuntimeEvent
from .line import RuntimeLine, RuntimeMode


def mode_runtime_line(
    mode: RuntimeMode | str,
    events: Iterable[RuntimeEvent],
    *,
    started_at: datetime | None = None,
    payload: Mapping[str, object] | None = None,
) -> RuntimeLine:
    values = tuple(events)
    runtime_mode = mode if isinstance(mode, RuntimeMode) else RuntimeMode(str(mode))
    if started_at is None and values:
        started_at = values[0].time
    prefix: tuple[RuntimeEvent, ...] = ()
    if started_at is not None:
        prefix = (
            SystemRuntimeEvent(
                f"runtime.mode.{runtime_mode.value}.started",
                1,
                started_at,
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
    payload: Mapping[str, object] | None = None,
) -> AccountRuntimeEvent:
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
    return AccountRuntimeEvent(
        context,
        sequence,
        at,
        payload={"equity": str(value), "source": source.value, **dict(payload or {})},
        snapshot=snapshot,
        stream=f"account.{context.environment.value}.{context.account.broker}.{context.account.account_id}",
    )


__all__ = ["account_baseline_event", "mode_runtime_line"]
