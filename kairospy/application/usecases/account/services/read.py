"""ExternalAccount read application service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from kairospy.application.usecases.account.protocol import (
    AccountQueryRequest,
    AccountReadMode,
    AccountReadPort,
    AccountReadRequest,
    AccountRefreshRequest,
    AccountRuntimeStateReader,
)
from kairospy.domain.account import AccountRuntimeContext, AccountSnapshot, AccountState, derive_account_state


@dataclass(frozen=True, slots=True)
class AccountReadResult:
    snapshot: AccountSnapshot
    account_state: AccountState


@dataclass(frozen=True, slots=True)
class AccountQueryResult:
    """A typed view of the current account state.

    ``stale`` is computed at query time, so callers do not accidentally treat
    an old snapshot as fresh merely because it is present in memory.
    """

    snapshot: AccountSnapshot | None
    account_state: AccountState | None
    stale: bool
    age_seconds: float | None
    mode: AccountReadMode


@dataclass(frozen=True, slots=True)
class AccountRefreshResult:
    """The state produced by one broker refresh and installed by the owner."""

    read: AccountReadResult
    refreshed: bool = True


@dataclass(frozen=True, slots=True)
class AccountReadService:
    reader: AccountReadPort

    def read(
        self,
        context: AccountRuntimeContext,
        *,
        symbol: str | None = None,
        at: datetime | None = None,
        fetch_orders: bool = True,
    ) -> AccountReadResult:
        observed_at = at or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            raise ValueError("account read timestamp must be timezone-aware")
        snapshot = self.reader.read_account(
            AccountReadRequest(context=context, observed_at=observed_at, symbol=symbol, fetch_orders=fetch_orders)
        )
        return AccountReadResult(snapshot, derive_account_state(context, venue=snapshot))


def query_account(
    source: "AccountRuntimeStateReader",
    context: AccountRuntimeContext,
    request: AccountQueryRequest,
) -> AccountQueryResult:
    """Read one cached account state with an explicit freshness policy."""

    snapshot = source.snapshot(request.account or context.segment)
    now = request.now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("account query timestamp must be timezone-aware")
    age = None if snapshot is None or snapshot.observed_at is None else max(
        0.0, (now - snapshot.observed_at).total_seconds()
    )
    stale = snapshot is None or snapshot.observed_at is None
    if request.max_age_seconds is not None and age is not None:
        stale = age > request.max_age_seconds
    state = None if snapshot is None else derive_account_state(
        context,
        venue=snapshot,
        max_snapshot_age_seconds=request.max_age_seconds,
        now=now if request.max_age_seconds is not None else None,
    )
    if request.mode is not AccountReadMode.CACHED:
        raise ValueError("refresh and reconcile queries must enter through the account actor")
    return AccountQueryResult(snapshot, state, stale, age, request.mode)


__all__ = [
    "AccountQueryRequest",
    "AccountQueryResult",
    "AccountReadMode",
    "AccountReadPort",
    "AccountReadRequest",
    "AccountReadResult",
    "AccountReadService",
    "AccountRefreshRequest",
    "AccountRefreshResult",
    "query_account",
]
