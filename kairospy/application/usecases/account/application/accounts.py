"""Public account capability for the account usecase."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

from kairospy.application.usecases.account.services.read import AccountReadResult
from kairospy.application.usecases.account.protocol import AccountLoginPort, AccountLoginResult, AccountSession, AccountMarketProfilePort
from kairospy.application.usecases.account.services.reconciliation import AccountEventFactory, AccountReconciliationResult
from kairospy.application.usecases.account.services.service import AccountService as AccountUsecaseService
from kairospy.application.usecases.account.services.snapshots import AccountSnapshotStore
from kairospy.domain.account import AccountBookRef, AccountCapability, AccountContext, AccountFeeSchedule, AccountMarketProfile, AccountSnapshot, AccountState
from kairospy.domain.reference import MarketRef


class AccountApplicationService:
    """Narrow account capability used by the system-level AccountService."""

    def __init__(
        self,
        contexts: Iterable[AccountContext] | AccountContext,
        *,
        ledger: object | None = None,
        account_reader: object | None = None,
        login_port: AccountLoginPort | None = None,
        snapshot_store: AccountSnapshotStore | None = None,
        capabilities: Iterable[AccountCapability] = (),
        fees: Iterable[AccountFeeSchedule] = (),
        snapshots: Iterable[AccountSnapshot] = (),
        account_event: AccountEventFactory | None = None,
        provision_missing_capabilities: bool = True,
        market_profile_port: AccountMarketProfilePort | None = None,
    ) -> None:
        self._service = AccountUsecaseService(
            contexts,
            ledger=ledger,
            account_reader=account_reader,
            login_port=login_port,
            snapshot_store=snapshot_store,
            capabilities=capabilities,
            fees=fees,
            snapshots=snapshots,
            account_event=account_event,
            provision_missing_capabilities=provision_missing_capabilities,
            market_profile_port=market_profile_port,
        )

    def accounts(self) -> tuple[AccountContext, ...]:
        return self._service.accounts()

    def login(self, account: AccountBookRef | None = None, *, credential_ref: str | None = None, connection_ids: tuple[str, ...] = (), at: datetime | None = None) -> AccountLoginResult:
        return self._service.login(account, credential_ref=credential_ref, connection_ids=connection_ids, at=at)

    def logout(self, session: AccountSession) -> None:
        self._service.logout(session)

    def capabilities(self, account: AccountBookRef | None = None) -> tuple[AccountCapability, ...]:
        return self._service.capabilities(account)

    def fees(self, account: AccountBookRef | None = None) -> tuple[AccountFeeSchedule, ...]:
        return self._service.fees(account)

    def market_profile(
        self,
        account: AccountBookRef,
        market: MarketRef,
        *,
        at: datetime | None = None,
        refresh: bool = False,
    ) -> AccountMarketProfile | None:
        return self._service.market_profile(account, market, at=at, refresh=refresh)

    def update_market_profile(self, profile: AccountMarketProfile) -> None:
        self._service.update_market_profile(profile)

    def market_profiles(self, account: AccountBookRef | None = None) -> tuple[AccountMarketProfile, ...]:
        return self._service.market_profiles(account)

    def snapshot(self, account: AccountBookRef | None = None) -> AccountSnapshot | None:
        return self._service.snapshot(account)

    def state(self, account: AccountBookRef | None = None) -> AccountState | None:
        return self._service.state(account)

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        self._service.update_snapshot(snapshot)

    def read(self, account: AccountBookRef | None = None, *, symbol: str | None = None, at: datetime | None = None, balance_options: Mapping[str, object] | None = None, order_options: Mapping[str, object] | None = None, fetch_orders: bool = True) -> AccountReadResult:
        return self._service.read(account, symbol=symbol, at=at, balance_params=balance_options, order_params=order_options, fetch_orders=fetch_orders)

    def reconcile(self, account: AccountBookRef | None = None, *, previous: AccountState | None = None, symbol: str | None = None, at: datetime | None = None, balance_options: Mapping[str, object] | None = None, order_options: Mapping[str, object] | None = None) -> AccountReconciliationResult:
        return self._service.reconcile(account, previous=previous, symbol=symbol, at=at, balance_params=balance_options, order_params=order_options)


__all__ = [
    "AccountApplicationService",
    "AccountLoginResult",
    "AccountSession",
    "AccountBookRef",
    "AccountCapability",
    "AccountContext",
    "AccountFeeSchedule",
    "AccountMarketProfile",
    "AccountSnapshot",
    "AccountState",
]
