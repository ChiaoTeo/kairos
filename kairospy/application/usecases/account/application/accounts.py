"""Public account capability for the account usecase."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from kairospy.application.usecases.account.services.read import AccountQueryResult, AccountRefreshResult
from kairospy.application.usecases.account.protocol import AccountLoginPort, AccountLoginResult, AccountSession, AccountMarketProfilePort, AccountQueryRequest, AccountRefreshRequest
from kairospy.application.usecases.account.application.reconciliation import AccountEventFactory, AccountReconciliationResult
from kairospy.application.usecases.account.services.service import AccountService as AccountUsecaseService
from kairospy.application.usecases.account.application.snapshots import AccountSnapshotStore
from kairospy.application.usecases.account.protocol import AccountReadPort
from kairospy.domain.account import AccountBalance, AccountLedger
from kairospy.domain.account import AccountSegment, AccountCapability, AccountRuntimeContext, AccountFeeSchedule, AccountMarketProfile, AccountSnapshot, AccountState, PositionSnapshot
from kairospy.domain.reference import MarketRef


class AccountApplicationService:
    """Narrow account capability used by the system-level AccountService."""

    def __init__(
        self,
        contexts: Iterable[AccountRuntimeContext] | AccountRuntimeContext,
        *,
        ledger: AccountLedger | None = None,
        account_reader: AccountReadPort | None = None,
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

    def accounts(self) -> tuple[AccountRuntimeContext, ...]:
        return self._service.accounts()

    def login(self, account: AccountSegment | None = None, *, credential_ref: str | None = None, connection_ids: tuple[str, ...] = (), at: datetime | None = None) -> AccountLoginResult:
        return self._service.login(account, credential_ref=credential_ref, connection_ids=connection_ids, at=at)

    def logout(self, session: AccountSession) -> None:
        self._service.logout(session)

    def capabilities(self, account: AccountSegment | None = None) -> tuple[AccountCapability, ...]:
        return self._service.capabilities(account)

    def fees(self, account: AccountSegment | None = None) -> tuple[AccountFeeSchedule, ...]:
        return self._service.fees(account)

    def assets(self, account: AccountSegment | None = None) -> tuple[AccountBalance, ...]:
        state = self.state(account)
        return () if state is None else state.balances

    def positions(self, account: AccountSegment | None = None) -> tuple[PositionSnapshot, ...]:
        state = self.state(account)
        return () if state is None else state.positions

    def market_profile(
        self,
        account: AccountSegment,
        market: MarketRef,
        *,
        at: datetime | None = None,
        refresh: bool = False,
    ) -> AccountMarketProfile | None:
        return self._service.market_profile(account, market, at=at, refresh=refresh)

    def update_market_profile(self, profile: AccountMarketProfile) -> None:
        self._service.update_market_profile(profile)

    def market_profiles(self, account: AccountSegment | None = None) -> tuple[AccountMarketProfile, ...]:
        return self._service.market_profiles(account)

    def snapshot(self, account: AccountSegment | None = None) -> AccountSnapshot | None:
        return self._service.snapshot(account)

    def state(self, account: AccountSegment | None = None, *, max_snapshot_age_seconds: int | None = None, now: datetime | None = None) -> AccountState | None:
        return self._service.state(account, max_snapshot_age_seconds=max_snapshot_age_seconds, now=now)

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        self._service.update_snapshot(snapshot)

    def query(self, request: AccountQueryRequest) -> AccountQueryResult:
        return self._service.query(request)

    def refresh(self, request: AccountRefreshRequest) -> AccountRefreshResult:
        return self._service.refresh(request)

    def reconcile(self, account: AccountSegment | None = None, *, previous: AccountState | None = None, symbol: str | None = None, at: datetime | None = None) -> AccountReconciliationResult:
        return self._service.reconcile(account, previous=previous, symbol=symbol, at=at)


__all__ = [
    "AccountApplicationService",
    "AccountLoginResult",
    "AccountSession",
    "AccountSegment",
    "AccountCapability",
    "AccountRuntimeContext",
    "AccountFeeSchedule",
    "AccountBalance",
    "AccountMarketProfile",
    "AccountSnapshot",
    "AccountState",
    "PositionSnapshot",
    "AccountQueryRequest",
    "AccountQueryResult",
    "AccountRefreshRequest",
    "AccountRefreshResult",
    "AccountReconciliationResult",
]
