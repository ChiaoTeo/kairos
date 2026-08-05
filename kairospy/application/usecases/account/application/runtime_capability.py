from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Protocol

from kairospy.application.usecases.account.application.accounts import AccountApplicationService
from kairospy.application.usecases.account.application.read import AccountReadResult
from kairospy.application.usecases.account.application.directory import AccountDirectory
from kairospy.application.usecases.account.application.accounts import (
    AccountBookRef,
    AccountCapability,
    AccountContext,
    AccountFeeSchedule,
    AccountMarketProfile,
    AccountSnapshot,
    AccountState,
)
from kairospy.application.usecases.account.protocol import AccountLoginResult, AccountSession
from kairospy.domain.reference import MarketRef


class AccountRuntimeCapability(Protocol):
    """The single account-state capability bound into one System instance."""

    def accounts(self) -> tuple[AccountContext, ...]: ...
    def login(self, account: AccountBookRef | None = None, *, credential_ref: str | None = None, connection_ids: tuple[str, ...] = (), at: datetime | None = None) -> AccountLoginResult: ...
    def logout(self, session: AccountSession) -> None: ...
    def capabilities(self, account: AccountBookRef | None = None) -> tuple[AccountCapability, ...]: ...
    def fees(self, account: AccountBookRef | None = None) -> tuple[AccountFeeSchedule, ...]: ...
    def market_profile(self, account: AccountBookRef, market: MarketRef, *, at: datetime | None = None, refresh: bool = False) -> AccountMarketProfile | None: ...
    def update_market_profile(self, profile: AccountMarketProfile) -> None: ...
    def market_profiles(self, account: AccountBookRef | None = None) -> tuple[AccountMarketProfile, ...]: ...
    def snapshot(self, account: AccountBookRef | None = None) -> AccountSnapshot | None: ...
    def state(self, account: AccountBookRef | None = None) -> AccountState | None: ...
    def update_snapshot(self, snapshot: AccountSnapshot) -> None: ...
    def read(self, account: AccountBookRef | None = None, *, symbol: str | None = None, at: datetime | None = None, balance_options: Mapping[str, object] | None = None, order_options: Mapping[str, object] | None = None, fetch_orders: bool = True) -> AccountReadResult: ...

    def directory(self) -> AccountDirectory: ...


class AccountRuntimeApplication:
    """Runtime-facing account capability owned by the account usecase."""

    def __init__(
        self,
        contexts: Iterable[AccountContext] | AccountContext | None = None,
        *,
        runtime: AccountRuntimeCapability | None = None,
        ledger: object | None = None,
        account_reader: object | None = None,
        login_port: object | None = None,
        snapshot_store: object | None = None,
        capabilities: Iterable[AccountCapability] = (),
        fees: Iterable[AccountFeeSchedule] = (),
        snapshots: Iterable[AccountSnapshot] = (),
        account_event: object | None = None,
        provision_missing_capabilities: bool = True,
        market_profile_port: object | None = None,
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

    def accounts(self) -> tuple[AccountContext, ...]:
        return self._runtime.accounts() if self._runtime is not None else self._service.accounts()

    def login(self, account: AccountBookRef | None = None, *, credential_ref: str | None = None, connection_ids: tuple[str, ...] = (), at: datetime | None = None) -> AccountLoginResult:
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

    def capabilities(self, account: AccountBookRef | None = None) -> tuple[AccountCapability, ...]:
        return self._runtime.capabilities(account) if self._runtime is not None else self._service.capabilities(account)

    def fees(self, account: AccountBookRef | None = None) -> tuple[AccountFeeSchedule, ...]:
        return self._runtime.fees(account) if self._runtime is not None else self._service.fees(account)

    def market_profile(self, account: AccountBookRef, market: MarketRef, *, at: datetime | None = None, refresh: bool = False) -> AccountMarketProfile | None:
        return self._runtime.market_profile(account, market, at=at, refresh=refresh) if self._runtime is not None else self._service.market_profile(account, market, at=at, refresh=refresh)

    def update_market_profile(self, profile: AccountMarketProfile) -> None:
        if self._runtime is not None:
            self._runtime.update_market_profile(profile)
        else:
            self._service.update_market_profile(profile)

    def market_profiles(self, account: AccountBookRef | None = None) -> tuple[AccountMarketProfile, ...]:
        return self._runtime.market_profiles(account) if self._runtime is not None else self._service.market_profiles(account)

    def snapshot(self, account: AccountBookRef | None = None) -> AccountSnapshot | None:
        return self._runtime.snapshot(account) if self._runtime is not None else self._service.snapshot(account)

    def state(self, account: AccountBookRef | None = None) -> AccountState | None:
        return self._runtime.state(account) if self._runtime is not None else self._service.state(account)

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        if self._runtime is not None:
            self._runtime.update_snapshot(snapshot)
        else:
            self._service.update_snapshot(snapshot)

    def read(
        self,
        account: AccountBookRef | None = None,
        *,
        symbol: str | None = None,
        at: datetime | None = None,
        balance_options: Mapping[str, object] | None = None,
        order_options: Mapping[str, object] | None = None,
        fetch_orders: bool = True,
    ) -> object:
        if self._runtime is not None:
            reader = getattr(self._runtime, "read", None)
            if not callable(reader):
                raise RuntimeError("bound account runtime does not support read")
            return reader(account, symbol=symbol, at=at, balance_options=balance_options, order_options=order_options, fetch_orders=fetch_orders)
        return self._service.read(account, symbol=symbol, at=at, balance_params=balance_options, order_params=order_options, fetch_orders=fetch_orders)

    def refresh(self) -> object:
        """Lifecycle refresh retained for runtime startup."""
        if self._runtime is not None:
            refresh = getattr(self._runtime, "refresh", None)
            if not callable(refresh):
                raise RuntimeError("bound account runtime does not support lifecycle refresh")
            return refresh()
        return self.read()

    def reconcile(
        self,
        account: AccountBookRef | None = None,
        *,
        previous: AccountState | None = None,
        symbol: str | None = None,
        at: datetime | None = None,
        balance_options: Mapping[str, object] | None = None,
        order_options: Mapping[str, object] | None = None,
    ) -> object:
        if self._runtime is not None:
            reconcile = getattr(self._runtime, "reconcile", None)
            if not callable(reconcile):
                raise RuntimeError("bound account runtime does not support reconcile")
            return reconcile(account, previous=previous, symbol=symbol, at=at, balance_options=balance_options, order_options=order_options)
        return self._service.reconcile(account, previous=previous, symbol=symbol, at=at, balance_params=balance_options, order_params=order_options)


__all__ = ["AccountRuntimeCapability", "AccountRuntimeApplication"]
