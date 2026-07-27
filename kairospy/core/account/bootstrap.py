from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Protocol, TYPE_CHECKING

from .model import AccountContext, AccountSnapshot
from .projection import AccountProjection
from kairospy.core.order import OrderState

if TYPE_CHECKING:
    from kairospy.core.execution import ExecutionCoordinator


class AccountBootstrapGateway(Protocol):
    def fetch_balance(self, *, params: Mapping[str, object] | None = None) -> Mapping[str, object]:
        ...

    def fetch_open_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        ...


class AccountBootstrapParser(Protocol):
    def snapshot(
        self,
        context: AccountContext,
        raw_balance: Mapping[str, object],
        raw_orders: tuple[Mapping[str, object], ...],
        *,
        observed_at: datetime,
    ) -> AccountSnapshot:
        ...

    def import_open_order(
        self,
        context: AccountContext,
        coordinator: ExecutionCoordinator,
        raw: Mapping[str, object],
        *,
        observed_at: datetime,
    ) -> OrderState:
        ...


@dataclass(frozen=True, slots=True)
class AccountBootstrapResult:
    snapshot: AccountSnapshot
    projection: AccountProjection
    imported_orders: tuple[OrderState, ...]


def bootstrap_account(
    context: AccountContext,
    gateway: AccountBootstrapGateway,
    coordinator: ExecutionCoordinator,
    parser: AccountBootstrapParser,
    *,
    symbol: str | None = None,
    at: datetime | None = None,
    balance_params: Mapping[str, object] | None = None,
    order_params: Mapping[str, object] | None = None,
) -> AccountBootstrapResult:
    observed_at = at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ValueError("bootstrap timestamp must be timezone-aware")

    raw_balance = gateway.fetch_balance(params=balance_params)
    raw_orders = tuple(gateway.fetch_open_orders(symbol, params=order_params))
    snapshot = parser.snapshot(context, raw_balance, raw_orders, observed_at=observed_at)
    imported = tuple(
        parser.import_open_order(context, coordinator, order, observed_at=observed_at)
        for order in raw_orders
    )
    projection = coordinator.account_projection(context, venue_snapshot=snapshot)
    return AccountBootstrapResult(snapshot, projection, imported)


__all__ = [
    "AccountBootstrapGateway",
    "AccountBootstrapParser",
    "AccountBootstrapResult",
    "bootstrap_account",
]
