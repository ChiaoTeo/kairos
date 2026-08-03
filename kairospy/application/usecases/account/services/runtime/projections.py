from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from kairospy.application.support.runtime.domain.accounts import RuntimeAccountDirectory
from kairospy.application.usecases.account.services.snapshots import AccountSnapshotService
from kairospy.domain.account import (
    AccountBalance,
    AccountCapability,
    AccountContext,
    AccountCurrentView,
    AccountDetailView,
    AccountEvent,
    AccountEventKind,
    AccountFeeSchedule,
    AccountSnapshot,
    AccountSource,
    AccountState,
    LiabilitySnapshot,
    MarginState,
    OpenOrderSnapshot,
    PositionSnapshot,
)


@dataclass(frozen=True, slots=True)
class RuntimeAccountViewProjectionService:
    runtime: object
    catalog: object | None = None
    account_directory: RuntimeAccountDirectory | None = None

    def directory(self) -> RuntimeAccountDirectory:
        if self.account_directory is not None:
            return self.account_directory
        if self.catalog is not None:
            return RuntimeAccountDirectory.from_contexts(tuple(self.catalog.accounts()))
        raise RuntimeError("runtime account catalog is required")

    def capabilities(self) -> tuple[AccountCapability, ...]:
        if self.catalog is None:
            return ()
        return tuple(self.catalog.capabilities())

    def fees(self) -> tuple[AccountFeeSchedule, ...]:
        if self.catalog is None:
            return ()
        return tuple(self.catalog.fees())

    def current_view(
        self,
        context: AccountContext,
        *,
        event_count: int = 0,
        last_event_time: datetime | None = None,
        payload: object | None = None,
        equity_currency: str | None = None,
        latest_equity: Decimal | None = None,
        initial_equity: Decimal | None = None,
    ) -> AccountCurrentView:
        state = self.runtime.state(context.book)
        snapshot = self.runtime.snapshot(context.book)
        balances = _balances(state, snapshot)
        margins = _margins(state, snapshot)
        liabilities = _liabilities(state, snapshot)
        positions = _positions(state, snapshot)
        open_orders = _open_orders(state, snapshot)
        cash = _cash(balances, equity_currency)
        equity = latest_equity or _payload_equity(payload) or cash
        baseline = initial_equity if initial_equity is not None else equity
        net_profit = None if equity is None or baseline is None else equity - baseline
        total_return = None if net_profit is None or baseline in (None, Decimal("0")) else net_profit / baseline
        return AccountCurrentView(
            context=context,
            identity=context.identity,
            book=context.book,
            book_kind=str(context.book.book),
            book_qualifier=context.book.qualifier,
            event_count=event_count,
            last_event_time=last_event_time,
            source=_source(state, snapshot),
            balances=balances,
            margins=margins,
            liabilities=liabilities,
            positions=positions,
            open_orders=open_orders,
            pending_orders=tuple(getattr(payload, "pending_orders", ()) or ()),
            stale=False if state is None else state.stale,
            cash=cash,
            equity=equity,
            initial_equity=baseline,
            net_profit=net_profit,
            total_return=total_return,
        )

    def detail_view(
        self,
        context: AccountContext,
        *,
        event_count: int = 0,
        last_event_time: datetime | None = None,
        metadata: dict[str, object] | None = None,
    ) -> AccountDetailView:
        return AccountDetailView(
            context=context,
            identity=context.identity,
            book=context.book,
            event_count=event_count,
            last_event_time=last_event_time,
            account_state=self.runtime.state(context.book),
            snapshot=self.runtime.snapshot(context.book),
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class RuntimeAccountProjectionService:
    account: AccountContext
    coordinator: object
    cash_currency: str = "USD"
    settlement_currency: str = "USD"

    def cash(self, currency: str | None = None) -> Decimal:
        return self.coordinator.ledger.cash(self.account.book).get(currency or self.cash_currency, Decimal("0"))

    def positions(self) -> dict[str, Decimal]:
        return dict(self.coordinator.ledger.positions(self.account.book))

    def record_funding(
        self,
        *,
        occurred_at: datetime,
        currency: str,
        cash_delta: Decimal,
        instrument_id: str,
        reference_id: str,
    ) -> None:
        self.coordinator.ledger.record(
            AccountEvent(
                uuid4(),
                self.account.book,
                AccountEventKind.FUNDING,
                occurred_at,
                currency,
                cash_delta=cash_delta,
                instrument_id=instrument_id,
                reference_id=reference_id,
            )
        )


@dataclass(frozen=True, slots=True)
class RuntimeAccountService:
    snapshots: AccountSnapshotService | None = None
    views: RuntimeAccountViewProjectionService | None = None
    projection: RuntimeAccountProjectionService | None = None

    def apply_snapshot(self, snapshot: AccountSnapshot) -> None:
        if self.snapshots is not None:
            self.snapshots.apply(snapshot)

    def directory(self) -> RuntimeAccountDirectory:
        return _require_account_views(self.views).directory()

    def capabilities(self) -> tuple[AccountCapability, ...]:
        return _require_account_views(self.views).capabilities()

    def fees(self) -> tuple[AccountFeeSchedule, ...]:
        return _require_account_views(self.views).fees()

    def current_view(
        self,
        context: AccountContext,
        *,
        event_count: int = 0,
        last_event_time: datetime | None = None,
        payload: object | None = None,
        equity_currency: str | None = None,
        latest_equity: Decimal | None = None,
        initial_equity: Decimal | None = None,
    ) -> AccountCurrentView:
        return _require_account_views(self.views).current_view(
            context,
            event_count=event_count,
            last_event_time=last_event_time,
            payload=payload,
            equity_currency=equity_currency,
            latest_equity=latest_equity,
            initial_equity=initial_equity,
        )

    def detail_view(
        self,
        context: AccountContext,
        *,
        event_count: int = 0,
        last_event_time: datetime | None = None,
        metadata: dict[str, object] | None = None,
    ) -> AccountDetailView:
        return _require_account_views(self.views).detail_view(
            context,
            event_count=event_count,
            last_event_time=last_event_time,
            metadata=metadata,
        )

    @property
    def account(self) -> AccountContext:
        return _require_account_projection(self.projection).account

    @property
    def cash_currency(self) -> str:
        return _require_account_projection(self.projection).cash_currency

    @property
    def settlement_currency(self) -> str:
        return _require_account_projection(self.projection).settlement_currency

    def cash(self, currency: str | None = None) -> Decimal:
        return _require_account_projection(self.projection).cash(currency)

    def positions(self) -> dict[str, Decimal]:
        return _require_account_projection(self.projection).positions()

    def record_funding(
        self,
        *,
        occurred_at: datetime,
        currency: str,
        cash_delta: Decimal,
        instrument_id: str,
        reference_id: str,
    ) -> None:
        _require_account_projection(self.projection).record_funding(
            occurred_at=occurred_at,
            currency=currency,
            cash_delta=cash_delta,
            instrument_id=instrument_id,
            reference_id=reference_id,
        )


def account_projection(
    account: object | None,
    catalog: object | None,
    execution: object | None,
) -> RuntimeAccountProjectionService | None:
    if account is None or catalog is None or execution is None:
        return None
    accounts = catalog.accounts()
    if len(accounts) != 1:
        return None
    service_account = getattr(account, "account", None)
    cash_currency = str(getattr(service_account, "cash_currency", "") or "USD")
    return RuntimeAccountProjectionService(
        account=accounts[0],
        coordinator=execution,
        cash_currency=cash_currency,
        settlement_currency=cash_currency,
    )


def _require_account_views(service: RuntimeAccountViewProjectionService | None) -> RuntimeAccountViewProjectionService:
    if service is None:
        raise RuntimeError("runtime account service has no view projection")
    return service


def _require_account_projection(service: RuntimeAccountProjectionService | None) -> RuntimeAccountProjectionService:
    if service is None:
        raise RuntimeError("runtime account service has no account projection")
    return service


def _payload_equity(payload: object | None) -> Decimal | None:
    value = getattr(payload, "equity", None)
    return None if value is None else Decimal(str(value))


def _balances(state: AccountState | None, snapshot: AccountSnapshot | None) -> tuple[AccountBalance, ...]:
    if state is not None:
        return state.balances
    return () if snapshot is None else snapshot.balances


def _margins(state: AccountState | None, snapshot: AccountSnapshot | None) -> tuple[MarginState, ...]:
    if state is not None:
        return state.margins
    return () if snapshot is None else snapshot.margins


def _liabilities(state: AccountState | None, snapshot: AccountSnapshot | None) -> tuple[LiabilitySnapshot, ...]:
    if state is not None:
        return state.liabilities
    return () if snapshot is None else snapshot.liabilities


def _positions(state: AccountState | None, snapshot: AccountSnapshot | None) -> tuple[PositionSnapshot, ...]:
    if state is not None:
        return state.positions
    return () if snapshot is None else snapshot.positions


def _open_orders(state: AccountState | None, snapshot: AccountSnapshot | None) -> tuple[OpenOrderSnapshot, ...]:
    if state is not None:
        return state.open_orders
    return () if snapshot is None else snapshot.open_orders


def _cash(balances: tuple[AccountBalance, ...], equity_currency: str | None) -> Decimal | None:
    currency = equity_currency
    if currency is None and balances:
        currency = balances[0].currency
    if currency is None:
        return None
    balance = next((item for item in balances if item.currency == currency), None)
    return None if balance is None else balance.total


def _source(
    state: AccountState | None,
    snapshot: AccountSnapshot | None,
) -> AccountSource | str | None:
    if state is not None:
        return state.source
    if snapshot is not None:
        return snapshot.source
    return None


__all__ = [
    "RuntimeAccountProjectionService",
    "RuntimeAccountService",
    "RuntimeAccountViewProjectionService",
    "account_projection",
]
