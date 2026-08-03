from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from kairospy.domain.account import AccountContext, AccountSnapshot, AccountState
from kairospy.domain.execution import ExecutionUpdate
from kairospy.domain.order import OrderState


@dataclass(frozen=True, slots=True)
class AccountBootstrapRequest:
    context: AccountContext
    observed_at: datetime
    symbol: str | None = None
    balance_params: Mapping[str, object] | None = None
    order_params: Mapping[str, object] | None = None
    fetch_orders: bool = True


@dataclass(frozen=True, slots=True)
class AccountBootstrapGatewayData:
    snapshot: AccountSnapshot
    imported_updates: tuple[ExecutionUpdate, ...] = ()


class AccountBootstrapGateway(Protocol):
    def bootstrap(self, request: AccountBootstrapRequest) -> AccountBootstrapGatewayData: ...


@dataclass(frozen=True, slots=True)
class AccountBootstrapResult:
    snapshot: AccountSnapshot
    account_state: AccountState
    imported_orders: tuple[OrderState, ...]


@dataclass(frozen=True, slots=True)
class AccountBootstrapService:
    gateway: AccountBootstrapGateway
    coordinator: object

    def bootstrap(
        self,
        context: AccountContext,
        *,
        symbol: str | None = None,
        at: datetime | None = None,
        balance_params: Mapping[str, object] | None = None,
        order_params: Mapping[str, object] | None = None,
        fetch_orders: bool = True,
    ) -> AccountBootstrapResult:
        observed_at = at or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            raise ValueError("bootstrap timestamp must be timezone-aware")

        data = self.gateway.bootstrap(
            AccountBootstrapRequest(
                context=context,
                observed_at=observed_at,
                symbol=symbol,
                balance_params=balance_params,
                order_params=order_params,
                fetch_orders=fetch_orders,
            )
        )
        snapshot = data.snapshot
        imported_orders = tuple(self.coordinator.apply_execution_update(update) for update in data.imported_updates)
        account_state = self.coordinator.account_projection(context, venue_snapshot=snapshot)
        return AccountBootstrapResult(snapshot, account_state, imported_orders)


__all__ = [
    "AccountBootstrapGateway",
    "AccountBootstrapGatewayData",
    "AccountBootstrapRequest",
    "AccountBootstrapResult",
    "AccountBootstrapService",
]
