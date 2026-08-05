from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from dataclasses import dataclass

from kairospy.domain.account import AccountRuntimeContext, AccountSnapshot, AccountState
from kairospy.application.usecases.account.application.reconciliation import AccountDifference, AccountEventFactory, AccountReconciliationResult
from kairospy.application.usecases.account.protocol import AccountReadPort
from .read import AccountReadResult, AccountReadService


@dataclass(frozen=True, slots=True)
class AccountReconciliationService:
    account: AccountRuntimeContext
    reader: AccountReadPort
    account_event: AccountEventFactory

    def reconcile(
        self,
        *,
        previous: AccountState | None = None,
        symbol: str | None = None,
        at: datetime | None = None,
    ) -> AccountReconciliationResult:
        observed_at = at or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            raise ValueError("reconciliation timestamp must be timezone-aware")
        read = AccountReadService(self.reader).read(
            self.account,
            symbol=symbol,
            at=observed_at,
        )
        differences = (
            ()
            if previous is None
            else compare_account_state(
                previous,
                read.snapshot,
            )
        )
        return AccountReconciliationResult(read, differences, self.account_event(observed_at, read.snapshot))


def compare_account_state(
    local: AccountState,
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
        if abs(left.reserved_amount - right.reserved_amount) > tolerance:
            differences.append(AccountDifference("open_order.reserved_amount", order_id, left.reserved_amount, right.reserved_amount))

    return tuple(differences)


__all__ = [
    "AccountDifference",
    "AccountEventFactory",
    "AccountReconciliationResult",
    "AccountReconciliationService",
    "compare_account_state",
]
