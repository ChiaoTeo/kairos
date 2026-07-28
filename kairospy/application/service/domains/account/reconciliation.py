from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Mapping

from kairospy.core.account import AccountContext, AccountState, AccountSnapshot
from kairospy.core.order import OrderState
from kairospy.core.execution import ExecutionCoordinator
from kairospy.application.runtime.model import RuntimeDataEnvelope

from .bootstrap import AccountBootstrapGateway, AccountBootstrapParser, AccountBootstrapResult, bootstrap_account


AccountEventFactory = Callable[[datetime, AccountSnapshot], RuntimeDataEnvelope]


@dataclass(frozen=True, slots=True)
class AccountDifference:
    kind: str
    key: str
    local: Decimal
    external: Decimal


@dataclass(frozen=True, slots=True)
class AccountReconciliationResult:
    bootstrap: AccountBootstrapResult
    differences: tuple[AccountDifference, ...]
    event: RuntimeDataEnvelope


@dataclass(frozen=True, slots=True)
class AccountReconciliationService:
    account: AccountContext
    gateway: AccountBootstrapGateway
    coordinator: ExecutionCoordinator
    parser: AccountBootstrapParser
    account_event: AccountEventFactory

    def reconcile(
        self,
        *,
        previous: AccountState | None = None,
        symbol: str | None = None,
        at: datetime | None = None,
        balance_params: Mapping[str, object] | None = None,
        order_params: Mapping[str, object] | None = None,
    ) -> AccountReconciliationResult:
        observed_at = at or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            raise ValueError("reconciliation timestamp must be timezone-aware")
        bootstrap = bootstrap_account(
            self.account,
            self.gateway,
            self.coordinator,
            self.parser,
            symbol=symbol,
            at=observed_at,
            balance_params=balance_params,
            order_params=order_params,
        )
        differences = (
            ()
            if previous is None
            else compare_account_state(
                previous,
                bootstrap.snapshot,
                pending_orders=self.coordinator.orders.active_for_context(self.account),
            )
        )
        return AccountReconciliationResult(
            bootstrap,
            differences,
            self.account_event(observed_at, bootstrap.snapshot),
        )


def compare_account_state(
    local: AccountState,
    external: AccountSnapshot,
    *,
    pending_orders: tuple[OrderState, ...] = (),
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

    for state in sorted(pending_orders, key=lambda item: item.venue_order_id or item.request.venue_order_id or item.local_order_id):
        venue_order_id = state.venue_order_id or state.request.venue_order_id
        if not venue_order_id:
            continue
        external_order = external_open_orders.get(venue_order_id)
        if external_order is None:
            differences.append(AccountDifference("pending_order.venue_present", venue_order_id, Decimal("1"), Decimal("0")))
            continue
        remaining = state.remaining_quantity
        if abs(remaining - external_order.quantity) > tolerance:
            differences.append(AccountDifference("pending_order.remaining_quantity", venue_order_id, remaining, external_order.quantity))

    return tuple(differences)


__all__ = [
    "AccountDifference",
    "AccountEventFactory",
    "AccountReconciliationResult",
    "AccountReconciliationService",
    "compare_account_state",
]
