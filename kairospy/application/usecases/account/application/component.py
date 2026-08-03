from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

from kairospy.application.usecases.account.services.service import AccountService
from kairospy.application.usecases.account.services.authorization import AccountTradeAuthorizationRequest, AccountTradeAuthorizationResult
from kairospy.application.usecases.account.services.bootstrap import AccountBootstrapResult
from kairospy.application.usecases.account.services.reconciliation import AccountEventFactory, AccountReconciliationResult
from kairospy.application.usecases.account.services.snapshots import AccountSnapshotStore
from kairospy.domain.account import AccountBookRef, AccountCapability, AccountContext, AccountFeeSchedule, AccountSnapshot, AccountState


class AccountApplication:
    """System-scoped public account component backed by private services."""

    def __init__(
        self,
        contexts: Iterable[AccountContext] | AccountContext,
        *,
        coordinator: object | None = None,
        bootstrap_gateway: object | None = None,
        snapshot_store: AccountSnapshotStore | None = None,
        capabilities: Iterable[AccountCapability] = (),
        fees: Iterable[AccountFeeSchedule] = (),
        snapshots: Iterable[AccountSnapshot] = (),
        account_event: AccountEventFactory | None = None,
        provision_missing_capabilities: bool = True,
    ) -> None:
        self._service = AccountService(
            contexts,
            coordinator=coordinator,
            bootstrap_gateway=bootstrap_gateway,
            snapshot_store=snapshot_store,
            capabilities=capabilities,
            fees=fees,
            snapshots=snapshots,
            account_event=account_event,
            provision_missing_capabilities=provision_missing_capabilities,
        )

    def accounts(self) -> tuple[AccountContext, ...]:
        return self._service.accounts()

    def capabilities(self, account: AccountBookRef | None = None) -> tuple[AccountCapability, ...]:
        return self._service.capabilities(account)

    def fees(self, account: AccountBookRef | None = None) -> tuple[AccountFeeSchedule, ...]:
        return self._service.fees(account)

    def snapshot(self, account: AccountBookRef | None = None) -> AccountSnapshot | None:
        return self._service.snapshot(account)

    def state(self, account: AccountBookRef | None = None) -> AccountState | None:
        return self._service.state(account)

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        self._service.update_snapshot(snapshot)

    def refresh(
        self,
        account: AccountBookRef | None = None,
        *,
        symbol: str | None = None,
        at: datetime | None = None,
        balance_options: Mapping[str, object] | None = None,
        order_options: Mapping[str, object] | None = None,
        fetch_orders: bool = True,
    ) -> AccountBootstrapResult:
        return self._service.refresh(
            account,
            symbol=symbol,
            at=at,
            balance_params=balance_options,
            order_params=order_options,
            fetch_orders=fetch_orders,
        )

    def reconcile(
        self,
        account: AccountBookRef | None = None,
        *,
        previous: AccountState | None = None,
        symbol: str | None = None,
        at: datetime | None = None,
        balance_options: Mapping[str, object] | None = None,
        order_options: Mapping[str, object] | None = None,
    ) -> AccountReconciliationResult:
        return self._service.reconcile(
            account,
            previous=previous,
            symbol=symbol,
            at=at,
            balance_params=balance_options,
            order_params=order_options,
        )

    def authorize_trade(self, request: AccountTradeAuthorizationRequest) -> AccountTradeAuthorizationResult:
        return self._service.authorize_trade(request)


__all__ = ["AccountApplication"]
