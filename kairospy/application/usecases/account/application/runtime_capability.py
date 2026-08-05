from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from kairospy.application.usecases.account.application.accounts import AccountApplicationService
from kairospy.application.usecases.account.application.read import (
    AccountQueryRequest,
    AccountQueryResult,
    AccountRefreshRequest,
    AccountRefreshResult,
)
from kairospy.application.usecases.account.application.directory import AccountDirectory
from kairospy.application.usecases.account.application.accounts import (
    AccountSegment,
    AccountCapability,
    AccountRuntimeContext,
    AccountFeeSchedule,
    AccountMarketProfile,
    AccountSnapshot,
    AccountState,
)
from kairospy.application.usecases.account.protocol import AccountLoginResult, AccountSession
from kairospy.application.usecases.account.protocol import AccountReadPort, AccountLoginPort, AccountMarketProfilePort
from kairospy.application.usecases.account.application.snapshots import AccountSnapshotStore
from kairospy.application.usecases.account.application.reconciliation import AccountEventFactory, AccountReconciliationResult
from kairospy.domain.account import AccountLedger
from kairospy.domain.account import AccountBalance, PositionSnapshot
from kairospy.domain.reference import MarketRef


class AccountRuntimeCapability(Protocol):
    """The single account-state capability bound into one System instance."""

    def accounts(self) -> tuple[AccountRuntimeContext, ...]: ...
    def login(self, account: AccountSegment | None = None, *, credential_ref: str | None = None, connection_ids: tuple[str, ...] = (), at: datetime | None = None) -> AccountLoginResult: ...
    def logout(self, session: AccountSession) -> None: ...
    def capabilities(self, account: AccountSegment | None = None) -> tuple[AccountCapability, ...]: ...
    def fees(self, account: AccountSegment | None = None) -> tuple[AccountFeeSchedule, ...]: ...
    def assets(self, account: AccountSegment | None = None) -> tuple[AccountBalance, ...]: ...
    def positions(self, account: AccountSegment | None = None) -> tuple[PositionSnapshot, ...]: ...
    def market_profile(self, account: AccountSegment, market: MarketRef, *, at: datetime | None = None, refresh: bool = False) -> AccountMarketProfile | None: ...
    def update_market_profile(self, profile: AccountMarketProfile) -> None: ...
    def market_profiles(self, account: AccountSegment | None = None) -> tuple[AccountMarketProfile, ...]: ...
    def snapshot(self, account: AccountSegment | None = None) -> AccountSnapshot | None: ...
    def state(self, account: AccountSegment | None = None) -> AccountState | None: ...
    def update_snapshot(self, snapshot: AccountSnapshot) -> None: ...
    def query(self, request: AccountQueryRequest) -> AccountQueryResult: ...
    def refresh_account(self, request: AccountRefreshRequest) -> AccountRefreshResult: ...
    def reconcile(self, account: AccountSegment | None = None, *, previous: AccountState | None = None, symbol: str | None = None, at: datetime | None = None) -> AccountReconciliationResult: ...

    def directory(self) -> AccountDirectory: ...


class AccountRuntimeApplication:
    """Runtime-facing account capability owned by the account usecase."""

    def __init__(
        self,
        contexts: Iterable[AccountRuntimeContext] | AccountRuntimeContext | None = None,
        *,
        runtime: AccountRuntimeCapability | None = None,
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
        if runtime is not None:
            if contexts is not None or any(
                value is not None for value in (ledger, account_reader, login_port, snapshot_store, account_event)
            ):
                raise ValueError("runtime account capability cannot be combined with account construction dependencies")
            self._runtime = runtime
            self._service: AccountApplicationService | None = None
            return
        if contexts is None:
            raise ValueError("account contexts are required when no runtime capability is supplied")
        self._runtime = None
        self._service = AccountApplicationService(
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
            market_profile_port=market_profile_port,  # type: ignore[arg-type]
        )

    def accounts(self) -> tuple[AccountRuntimeContext, ...]:
        return self._runtime.accounts() if self._runtime is not None else self._service.accounts()

    def login(self, account: AccountSegment | None = None, *, credential_ref: str | None = None, connection_ids: tuple[str, ...] = (), at: datetime | None = None) -> AccountLoginResult:
        return self._runtime.login(account, credential_ref=credential_ref, connection_ids=connection_ids, at=at) if self._runtime is not None else self._service.login(account, credential_ref=credential_ref, connection_ids=connection_ids, at=at)

    def logout(self, session: AccountSession) -> None:
        if self._runtime is not None:
            self._runtime.logout(session)
        else:
            self._service.logout(session)

    def directory(self) -> AccountDirectory:
        if self._runtime is not None:
            directory = getattr(self._runtime, "directory", None)
            if callable(directory):
                return directory()
        return AccountDirectory.from_contexts(self.accounts())

    def capabilities(self, account: AccountSegment | None = None) -> tuple[AccountCapability, ...]:
        return self._runtime.capabilities(account) if self._runtime is not None else self._service.capabilities(account)

    def fees(self, account: AccountSegment | None = None) -> tuple[AccountFeeSchedule, ...]:
        return self._runtime.fees(account) if self._runtime is not None else self._service.fees(account)

    def assets(self, account: AccountSegment | None = None) -> tuple[AccountBalance, ...]:
        return self._runtime.assets(account) if self._runtime is not None else self._service.assets(account)

    def positions(self, account: AccountSegment | None = None) -> tuple[PositionSnapshot, ...]:
        return self._runtime.positions(account) if self._runtime is not None else self._service.positions(account)

    def market_profile(self, account: AccountSegment, market: MarketRef, *, at: datetime | None = None, refresh: bool = False) -> AccountMarketProfile | None:
        return self._runtime.market_profile(account, market, at=at, refresh=refresh) if self._runtime is not None else self._service.market_profile(account, market, at=at, refresh=refresh)

    def update_market_profile(self, profile: AccountMarketProfile) -> None:
        if self._runtime is not None:
            self._runtime.update_market_profile(profile)
        else:
            self._service.update_market_profile(profile)

    def market_profiles(self, account: AccountSegment | None = None) -> tuple[AccountMarketProfile, ...]:
        return self._runtime.market_profiles(account) if self._runtime is not None else self._service.market_profiles(account)

    def snapshot(self, account: AccountSegment | None = None) -> AccountSnapshot | None:
        return self._runtime.snapshot(account) if self._runtime is not None else self._service.snapshot(account)

    def state(self, account: AccountSegment | None = None) -> AccountState | None:
        return self._runtime.state(account) if self._runtime is not None else self._service.state(account)

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        if self._runtime is not None:
            self._runtime.update_snapshot(snapshot)
        else:
            self._service.update_snapshot(snapshot)

    def query(self, request: AccountQueryRequest) -> AccountQueryResult:
        if self._runtime is not None:
            query = getattr(self._runtime, "query", None)
            if not callable(query):
                raise RuntimeError("bound account runtime does not support query")
            return query(request)
        return self._service.query(request)

    def reconcile(self, account: AccountSegment | None = None, *, previous: AccountState | None = None, symbol: str | None = None, at: datetime | None = None) -> AccountReconciliationResult:
        return self._runtime.reconcile(account, previous=previous, symbol=symbol, at=at) if self._runtime is not None else self._service.reconcile(account, previous=previous, symbol=symbol, at=at)

    def refresh(self, request: AccountRefreshRequest | None = None) -> AccountRefreshResult | AccountSnapshot:
        """Refresh account state; without a request, serve the lifecycle hook."""
        if request is None:
            if self._runtime is not None:
                refresh = getattr(self._runtime, "refresh", None)
                if not callable(refresh):
                    raise RuntimeError("bound account runtime does not support lifecycle refresh")
                return refresh()
            return self._service.refresh(AccountRefreshRequest())
        if self._runtime is not None:
            refresh = getattr(self._runtime, "refresh_account", None)
            if not callable(refresh):
                raise RuntimeError("bound account runtime does not support account refresh")
            return refresh(request)
        return self._service.refresh(request)


__all__ = ["AccountRuntimeCapability", "AccountRuntimeApplication"]
