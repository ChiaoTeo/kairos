from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from .ledger import AccountLedger
from .model import (
    AccountBalance,
    AccountContext,
    AccountRef,
    AccountSnapshot,
    AccountSource,
    MarginState,
    OpenOrderSnapshot,
    PositionSnapshot,
)


class AccountHoldSource(Protocol):
    def active_amounts(self, account: AccountRef) -> dict[str, Decimal]:
        ...


@dataclass(frozen=True, slots=True)
class AccountState:
    context: AccountContext
    balances: tuple[AccountBalance, ...]
    margins: tuple[MarginState, ...]
    positions: tuple[PositionSnapshot, ...]
    open_orders: tuple[OpenOrderSnapshot, ...]
    observed_at: datetime | None
    source: AccountSource
    stale: bool = False

    def balance(self, currency: str) -> AccountBalance | None:
        return next((item for item in self.balances if item.currency == currency), None)


def derive_account_state(
    context: AccountContext,
    *,
    ledger: AccountLedger | None = None,
    venue: AccountSnapshot | None = None,
    holds: AccountHoldSource | None = None,
    max_snapshot_age_seconds: int | None = None,
    now: datetime | None = None,
) -> AccountState:
    if venue is not None and venue.context != context:
        raise ValueError("venue snapshot context does not match account state context")
    account = context.account

    stale = _is_stale(venue, max_snapshot_age_seconds, now)
    if venue is not None:
        balances = {item.currency: item for item in venue.balances}
        margins = venue.margins
        positions = {item.instrument_id: item for item in venue.positions}
        open_orders = {item.order_id: item for item in venue.open_orders}
        observed_at = venue.observed_at
        source = AccountSource.STALE if stale else venue.source
    else:
        balances = {}
        margins = ()
        positions = {}
        open_orders = {}
        observed_at = None
        source = AccountSource.LEDGER

    if ledger is not None and venue is None:
        for currency, amount in ledger.cash(account).items():
            balances[currency] = AccountBalance.from_total_locked(
                currency,
                amount,
                Decimal("0"),
                source=AccountSource.LEDGER,
            )
        for instrument_id, quantity in ledger.positions(account).items():
            positions[instrument_id] = PositionSnapshot(instrument_id, quantity, AccountSource.LEDGER)

    if holds is not None:
        for currency, held in holds.active_amounts(account).items():
            balance = balances.get(currency)
            if balance is None:
                balances[currency] = AccountBalance.from_free_locked(
                    currency,
                    Decimal("0") - held,
                    held,
                    source=AccountSource.MODEL,
                )
            else:
                balances[currency] = AccountBalance(
                    balance.currency,
                    balance.total,
                    balance.free - held,
                    balance.locked + held,
                    AccountSource.MIXED if balance.source is not AccountSource.MODEL else AccountSource.MODEL,
                )
            source = AccountSource.MIXED

    return AccountState(
        context,
        tuple(sorted(balances.values(), key=lambda item: item.currency)),
        tuple(margins),
        tuple(sorted(positions.values(), key=lambda item: item.instrument_id)),
        tuple(sorted(open_orders.values(), key=lambda item: item.order_id)),
        observed_at,
        source,
        stale,
    )


def _is_stale(
    venue: AccountSnapshot | None,
    max_snapshot_age_seconds: int | None,
    now: datetime | None,
) -> bool:
    if venue is None or max_snapshot_age_seconds is None:
        return False
    if max_snapshot_age_seconds < 0:
        raise ValueError("max snapshot age cannot be negative")
    if venue.observed_at is None:
        return True
    if now is None:
        raise ValueError("now is required when max_snapshot_age_seconds is set")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return (now - venue.observed_at).total_seconds() > max_snapshot_age_seconds


__all__ = [
    "AccountState",
    "AccountHoldSource",
    "derive_account_state",
]
