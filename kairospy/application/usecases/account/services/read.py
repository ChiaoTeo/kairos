"""Account read application service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from kairospy.application.usecases.account.protocol import AccountReadPort, AccountReadRequest
from kairospy.domain.account import AccountContext, AccountSnapshot, AccountState, derive_account_state


@dataclass(frozen=True, slots=True)
class AccountReadResult:
    snapshot: AccountSnapshot
    account_state: AccountState


@dataclass(frozen=True, slots=True)
class AccountReadService:
    reader: AccountReadPort

    def read(
        self,
        context: AccountContext,
        *,
        symbol: str | None = None,
        at: datetime | None = None,
        options: Mapping[str, object] | None = None,
        fetch_orders: bool = True,
    ) -> AccountReadResult:
        observed_at = at or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            raise ValueError("account read timestamp must be timezone-aware")
        snapshot = self.reader.read_account(
            AccountReadRequest(
                context=context,
                observed_at=observed_at,
                symbol=symbol,
                options=options,
                fetch_orders=fetch_orders,
            )
        )
        return AccountReadResult(snapshot, derive_account_state(context, venue=snapshot))


__all__ = ["AccountReadPort", "AccountReadRequest", "AccountReadResult", "AccountReadService"]
