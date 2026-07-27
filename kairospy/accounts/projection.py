from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Iterable

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
from .reservation import ReservationBook

if TYPE_CHECKING:
    from kairospy.orders import OrderState


@dataclass(frozen=True, slots=True)
class AccountProjection:
    context: AccountContext
    balances: tuple[AccountBalance, ...]
    margins: tuple[MarginState, ...]
    positions: tuple[PositionSnapshot, ...]
    open_orders: tuple[OpenOrderSnapshot, ...]
    pending_orders: tuple["OrderState", ...]
    observed_at: datetime | None
    source: AccountSource
    stale: bool = False

    def balance(self, currency: str) -> AccountBalance | None:
        return next((item for item in self.balances if item.currency == currency), None)


@dataclass(frozen=True, slots=True)
class AccountDifference:
    kind: str
    key: str
    local: Decimal
    external: Decimal


def project_account(
    context: AccountContext,
    *,
    ledger: AccountLedger | None = None,
    venue: AccountSnapshot | None = None,
    reservations: ReservationBook | None = None,
    local_orders: Iterable["OrderState"] | None = None,
    max_snapshot_age_seconds: int | None = None,
    now: datetime | None = None,
) -> AccountProjection:
    if venue is not None and venue.context != context:
        raise ValueError("venue snapshot context does not match projection context")
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

    if reservations is not None:
        for currency, held in reservations.active_amounts(account).items():
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

    pending_orders = tuple(
        sorted(
            (
                state
                for state in (local_orders or ())
                if state.request.context == context and not state.status.terminal
            ),
            key=lambda state: state.local_order_id,
        )
    )
    if pending_orders:
        source = AccountSource.MIXED

    return AccountProjection(
        context,
        tuple(sorted(balances.values(), key=lambda item: item.currency)),
        tuple(margins),
        tuple(sorted(positions.values(), key=lambda item: item.instrument_id)),
        tuple(sorted(open_orders.values(), key=lambda item: item.order_id)),
        pending_orders,
        observed_at,
        source,
        stale,
    )


def compare_account_state(
    local: AccountProjection,
    external: AccountSnapshot,
    *,
    tolerance: Decimal = Decimal("0.00000001"),
) -> tuple[AccountDifference, ...]:
    if local.context != external.context:
        raise ValueError("cannot compare different account contexts")
    differences: list[AccountDifference] = []

    local_balances = {item.currency: item for item in local.balances}
    external_balances = {item.currency: item for item in external.balances}
    for currency in sorted(set(local_balances) | set(external_balances)):
        left = local_balances.get(currency)
        right = external_balances.get(currency)
        for field in ("total", "free", "locked"):
            local_value = getattr(left, field) if left else Decimal("0")
            external_value = getattr(right, field) if right else Decimal("0")
            if abs(local_value - external_value) > tolerance:
                differences.append(AccountDifference(f"balance.{field}", currency, local_value, external_value))

    local_positions = {item.instrument_id: item.quantity for item in local.positions}
    external_positions = {item.instrument_id: item.quantity for item in external.positions}
    for instrument_id in sorted(set(local_positions) | set(external_positions)):
        local_value = local_positions.get(instrument_id, Decimal("0"))
        external_value = external_positions.get(instrument_id, Decimal("0"))
        if abs(local_value - external_value) > tolerance:
            differences.append(AccountDifference("position.quantity", instrument_id, local_value, external_value))

    local_open_orders = {item.order_id: item for item in local.open_orders}
    external_open_orders = {item.order_id: item for item in external.open_orders}
    for order_id in sorted(set(local_open_orders) | set(external_open_orders)):
        left = local_open_orders.get(order_id)
        right = external_open_orders.get(order_id)
        if left is None or right is None:
            differences.append(
                AccountDifference(
                    "open_order.present",
                    order_id,
                    Decimal("1") if left is not None else Decimal("0"),
                    Decimal("1") if right is not None else Decimal("0"),
                )
            )
            continue
        if abs(left.quantity - right.quantity) > tolerance:
            differences.append(AccountDifference("open_order.quantity", order_id, left.quantity, right.quantity))
        if left.reserved_amount != right.reserved_amount:
            if abs(left.reserved_amount - right.reserved_amount) > tolerance:
                differences.append(
                    AccountDifference("open_order.reserved_amount", order_id, left.reserved_amount, right.reserved_amount)
                )

    external_by_venue_id = external_open_orders
    for state in local.pending_orders:
        venue_order_id = state.venue_order_id or state.request.venue_order_id
        if not venue_order_id:
            continue
        external_order = external_by_venue_id.get(venue_order_id)
        if external_order is None:
            differences.append(AccountDifference("pending_order.venue_present", venue_order_id, Decimal("1"), Decimal("0")))
            continue
        remaining = state.remaining_quantity
        if abs(remaining - external_order.quantity) > tolerance:
            differences.append(AccountDifference("pending_order.remaining_quantity", venue_order_id, remaining, external_order.quantity))

    return tuple(differences)


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
    "AccountDifference",
    "AccountProjection",
    "compare_account_state",
    "project_account",
]
