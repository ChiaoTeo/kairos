from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from kairospy.application.usecases.account.application.directory import AccountDirectory
from kairospy.application.usecases.account.application.snapshots import AccountSnapshotService
from kairospy.application.usecases.account.protocol import AccountCatalogReader, AccountRuntimeStateReader
from kairospy.domain.account import (
    AccountLedger,
    AccountBalance,
    CollateralBalance,
    AccountSegment,
    AccountCapability,
    AccountRuntimeContext,
    AccountCurrentView,
    AccountDetailView,
    AccountEvent,
    AccountEventKind,
    AccountFeeSchedule,
    AccountMarketProfile,
    AccountSnapshot,
    AssetCode,
    AccountSource,
    AccountState,
    LiabilitySnapshot,
    MarginState,
    OpenOrderSnapshot,
    PositionSnapshot,
)
from kairospy.domain.order import OrderState
from kairospy.application.usecases.account.application.view_contracts import AccountViewObservation

@dataclass(frozen=True, slots=True)
class RuntimeAccountViewProjectionService:
    runtime: AccountRuntimeStateReader
    catalog: AccountCatalogReader | None = None
    account_directory: AccountDirectory | None = None
    max_snapshot_age_seconds: int | None = 300

    def directory(self) -> AccountDirectory:
        if self.account_directory is not None:
            return self.account_directory
        if self.catalog is not None:
            return AccountDirectory.from_contexts(tuple(self.catalog.accounts()))
        raise RuntimeError("runtime account catalog is required")

    def capabilities(self) -> tuple[AccountCapability, ...]:
        if self.catalog is None:
            return ()
        return tuple(self.catalog.capabilities())

    def fees(self) -> tuple[AccountFeeSchedule, ...]:
        if self.catalog is None:
            return ()
        return tuple(self.catalog.fees())

    def market_profiles(self) -> tuple[AccountMarketProfile, ...]:
        if self.catalog is None:
            return ()
        profiles = getattr(self.catalog, "market_profiles", None)
        return () if not callable(profiles) else tuple(profiles())

    def current_view(
        self,
        context: AccountRuntimeContext,
        *,
        event_count: int = 0,
        last_event_time: datetime | None = None,
        payload: AccountViewObservation | None = None,
        equity_currency: AssetCode | str | None = None,
        latest_equity: Decimal | None = None,
        initial_equity: Decimal | None = None,
        pending_orders: tuple[OrderState, ...] = (),
        now: datetime | None = None,
    ) -> AccountCurrentView:
        state = _runtime_state(self.runtime, context.segment, max_snapshot_age_seconds=self.max_snapshot_age_seconds, now=now)
        snapshot = self.runtime.snapshot(context.segment)
        balances = _balances(state, snapshot)
        margins = _margins(state, snapshot)
        collaterals = () if state is None else state.collaterals
        liabilities = _liabilities(state, snapshot)
        positions = _positions(state, snapshot)
        open_orders = _open_orders(state, snapshot)
        simulated_orders = _simulated_open_orders(context, pending_orders)
        if simulated_orders and snapshot is None:
            open_orders = simulated_orders
        valuation_asset = _valuation_asset(balances, equity_currency)
        selected_balance = _selected_balance(balances, valuation_asset)
        equity = latest_equity or _payload_equity(payload) or selected_balance
        baseline = initial_equity if initial_equity is not None else equity
        net_profit = None if equity is None or baseline is None else equity - baseline
        total_return = None if net_profit is None or baseline in (None, Decimal("0")) else net_profit / baseline
        return AccountCurrentView(
            context=context,
            identity=context.identity,
            segment=context.segment,
            segment_model=context.segment.model.value,
            segment_qualifier=context.segment.qualifier,
            event_count=event_count,
            last_event_time=last_event_time,
            source=_source(state, snapshot),
            balances=balances,
            margins=margins,
            collaterals=collaterals,
            liabilities=liabilities,
            positions=positions,
            open_orders=open_orders,
            pending_orders=pending_orders,
            stale=False if state is None else state.stale,
            selected_balance=selected_balance,
            equity=equity,
            initial_equity=baseline,
            net_profit=net_profit,
            total_return=total_return,
            valuation_asset=valuation_asset,
        )

    def detail_view(
        self,
        context: AccountRuntimeContext,
        *,
        event_count: int = 0,
        last_event_time: datetime | None = None,
        metadata: dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> AccountDetailView:
        return AccountDetailView(
            context=context,
            identity=context.identity,
            segment=context.segment,
            event_count=event_count,
            last_event_time=last_event_time,
            account_state=_runtime_state(self.runtime, context.segment, max_snapshot_age_seconds=self.max_snapshot_age_seconds, now=now),
            snapshot=self.runtime.snapshot(context.segment),
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class RuntimeAccountProjectionService:
    account: AccountRuntimeContext
    ledger: AccountLedger
    valuation_asset: AssetCode = AssetCode("USD")
    settlement_asset: AssetCode = AssetCode("USD")

    def asset_balance(self, currency: AssetCode | str | None = None) -> Decimal:
        selected = self.valuation_asset if currency is None else currency
        selected = selected if isinstance(selected, AssetCode) else AssetCode(selected)
        return self.ledger.balances(self.account.segment).get(selected, Decimal("0"))

    def positions(self) -> dict[str, Decimal]:
        return dict(self.ledger.positions(self.account.segment))

    def record_funding(
        self,
        *,
        occurred_at: datetime,
        currency: AssetCode | str,
        balance_delta: Decimal,
        instrument_id: str,
        reference_id: str,
    ) -> None:
        self.ledger.record(
            AccountEvent(
                uuid4(),
                self.account.segment,
                AccountEventKind.FUNDING,
                occurred_at,
                currency,
                balance_delta=balance_delta,
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

    def directory(self) -> AccountDirectory:
        return _require_account_views(self.views).directory()

    def capabilities(self) -> tuple[AccountCapability, ...]:
        return _require_account_views(self.views).capabilities()

    def fees(self) -> tuple[AccountFeeSchedule, ...]:
        return _require_account_views(self.views).fees()

    def market_profiles(self) -> tuple[AccountMarketProfile, ...]:
        return _require_account_views(self.views).market_profiles()

    def current_view(
        self,
        context: AccountRuntimeContext,
        *,
        event_count: int = 0,
        last_event_time: datetime | None = None,
        payload: AccountViewObservation | None = None,
        equity_currency: AssetCode | str | None = None,
        latest_equity: Decimal | None = None,
        initial_equity: Decimal | None = None,
        pending_orders: tuple[OrderState, ...] = (),
        now: datetime | None = None,
    ) -> AccountCurrentView:
        return _require_account_views(self.views).current_view(
            context,
            event_count=event_count,
            last_event_time=last_event_time,
            payload=payload,
            equity_currency=equity_currency,
            latest_equity=latest_equity,
            initial_equity=initial_equity,
            pending_orders=pending_orders,
            now=now,
        )

    def detail_view(
        self,
        context: AccountRuntimeContext,
        *,
        event_count: int = 0,
        last_event_time: datetime | None = None,
        metadata: dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> AccountDetailView:
        return _require_account_views(self.views).detail_view(
            context,
            event_count=event_count,
            last_event_time=last_event_time,
            metadata=metadata,
            now=now,
        )

    @property
    def account(self) -> AccountRuntimeContext:
        return _require_account_projection(self.projection).account

    @property
    def valuation_asset(self) -> AssetCode:
        return _require_account_projection(self.projection).valuation_asset

    @property
    def settlement_asset(self) -> AssetCode:
        return _require_account_projection(self.projection).settlement_asset

    def asset_balance(self, currency: AssetCode | str | None = None) -> Decimal:
        return _require_account_projection(self.projection).asset_balance(currency)

    def positions(self) -> dict[str, Decimal]:
        return _require_account_projection(self.projection).positions()

    def record_funding(
        self,
        *,
        occurred_at: datetime,
        currency: AssetCode | str,
        balance_delta: Decimal,
        instrument_id: str,
        reference_id: str,
    ) -> None:
        _require_account_projection(self.projection).record_funding(
            occurred_at=occurred_at,
            currency=currency,
            balance_delta=balance_delta,
            instrument_id=instrument_id,
            reference_id=reference_id,
        )


def account_projection(
    account: AccountRuntimeStateReader | None,
    catalog: AccountCatalogReader | None,
    ledger: AccountLedger | None,
) -> RuntimeAccountProjectionService | None:
    if account is None or catalog is None or ledger is None:
        return None
    accounts = catalog.accounts()
    if len(accounts) != 1:
        return None
    service_account = getattr(account, "account", None)
    valuation_asset = AssetCode(str(getattr(service_account, "valuation_asset", "") or "USD"))
    return RuntimeAccountProjectionService(
        account=accounts[0],
        ledger=ledger,
        valuation_asset=valuation_asset,
        settlement_asset=valuation_asset,
    )


def _require_account_views(service: RuntimeAccountViewProjectionService | None) -> RuntimeAccountViewProjectionService:
    if service is None:
        raise RuntimeError("runtime account service has no view projection")
    return service


def _runtime_state(runtime: AccountRuntimeStateReader, account: AccountSegment, *, max_snapshot_age_seconds: int | None, now: datetime | None) -> AccountState | None:
    return runtime.state(account, max_snapshot_age_seconds=max_snapshot_age_seconds, now=now)


def _require_account_projection(service: RuntimeAccountProjectionService | None) -> RuntimeAccountProjectionService:
    if service is None:
        raise RuntimeError("runtime account service has no account projection")
    return service


def _payload_equity(payload: AccountViewObservation | None) -> Decimal | None:
    return None if payload is None else payload.equity


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


def _simulated_open_orders(context: AccountRuntimeContext, orders: tuple[OrderState, ...]) -> tuple[OpenOrderSnapshot, ...]:
    if context.environment.value not in {"paper", "backtest", "simulation"}:
        return ()
    values = []
    for order in orders:
        request = order.request
        values.append(
            OpenOrderSnapshot(
                str(order.order_id),
                request.instrument_id,
                request.side.value,
                order.remaining_quantity,
                AccountSource.SIMULATED,
            )
        )
    return tuple(values)


def _valuation_asset(balances: tuple[AccountBalance, ...], equity_currency: AssetCode | str | None) -> AssetCode | None:
    if equity_currency is not None:
        return equity_currency if isinstance(equity_currency, AssetCode) else AssetCode(equity_currency)
    currencies = {item.currency for item in balances}
    return next(iter(currencies)) if len(currencies) == 1 else None


def _selected_balance(balances: tuple[AccountBalance, ...], currency: AssetCode | None) -> Decimal | None:
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
