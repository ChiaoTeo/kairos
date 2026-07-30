from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import uuid4

from kairospy.application.launch import LaunchAccountDirectory
from kairospy.application.ports import AccountPort, DataSubscription, MarketDataPort, ReferencePort, TradingExecutionPort
from kairospy.application.service.runtime.account.snapshots import AccountSnapshotStore, ApplyAccountSnapshotUseCase
from kairospy.application.service.runtime.execution.updates import ApplyExecutionUpdateUseCase
from kairospy.core.account import (
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
from kairospy.core.execution import (
    ExecutionCoordinator,
    ExecutionCurrentView,
    ExecutionFillSummary,
    ExecutionFillsView,
    ExecutionOrderSummary,
)
from kairospy.core.intent import IntentJournal
from kairospy.core.intent import TradeIntent
from kairospy.core.order import OrderState
from kairospy.core.reference import LifecycleEvent, ReferenceCatalog


@dataclass(frozen=True, slots=True)
class RuntimeServiceDependencies:
    intents: IntentJournal
    data: MarketDataPort | None = None
    account_snapshot_store: AccountSnapshotStore | None = None
    account: AccountPort | None = None
    reference: ReferencePort | None = None
    trading_execution: TradingExecutionPort | None = None
    execution: ExecutionCoordinator | None = None
    fills_source: object | None = None


class RuntimeAccountIndexService(Protocol):
    def directory(self) -> LaunchAccountDirectory:
        ...

    def capabilities(self) -> tuple[AccountCapability, ...]:
        ...

    def fees(self) -> tuple[AccountFeeSchedule, ...]:
        ...


@dataclass(frozen=True, slots=True)
class RuntimeExecutionProjectionService:
    coordinator: ExecutionCoordinator
    fills_source: object | None = None

    def order_states(self) -> tuple[OrderState, ...]:
        return tuple(self.coordinator.orders.states)

    def pending_reservation_count(self) -> int:
        return sum(
            1
            for reservation in self.coordinator.reservations.reservations
            if reservation.status.value in {"held", "reflected"}
        )

    def ledger_event_count(self) -> int:
        return len(self.coordinator.ledger.events)

    def fills(self) -> tuple[object, ...]:
        return tuple(getattr(self.fills_source, "fills", ()) or ())

    def market_id_for_order(self, order_id: str) -> str | None:
        if not order_id:
            return None
        for order in self.coordinator.orders.states:
            if order.order_id == order_id:
                return _optional_text(order.request.market_id)
        return None

    def current_view(self) -> ExecutionCurrentView:
        orders = tuple(_execution_order_summary(order) for order in self.order_states())
        active = tuple(item for item in orders if item.status not in {"filled", "canceled", "rejected", "expired"})
        return ExecutionCurrentView(
            total_orders=len(orders),
            active_orders=len(active),
            terminal_orders=len(orders) - len(active),
            unknown_orders=sum(1 for item in orders if item.status == "unknown"),
            pending_reservations=self.pending_reservation_count(),
            ledger_event_count=self.ledger_event_count(),
            latest_order=_latest_order(orders),
            orders=orders,
        )

    def fills_view(self) -> ExecutionFillsView:
        fills = tuple(_fill_summary(fill, market_id=self.market_id_for_order(str(getattr(fill, "order_id", "") or ""))) for fill in self.fills())
        return ExecutionFillsView(total_fills=len(fills), fills=fills)


@dataclass(frozen=True, slots=True)
class RuntimeAccountViewProjectionService:
    port: AccountPort

    def directory(self) -> LaunchAccountDirectory:
        provider = getattr(self.port, "directory", None)
        if provider is not None:
            try:
                directory = provider()
            except TypeError:
                directory = None
            if isinstance(directory, LaunchAccountDirectory):
                return directory
        return LaunchAccountDirectory.from_contexts(tuple(self.port.accounts()))

    def capabilities(self) -> tuple[AccountCapability, ...]:
        provider = getattr(self.port, "capabilities", None)
        if provider is None:
            return ()
        try:
            return tuple(provider())
        except TypeError:
            return ()

    def fees(self) -> tuple[AccountFeeSchedule, ...]:
        provider = getattr(self.port, "fees", None)
        if provider is None:
            return ()
        try:
            return tuple(provider())
        except TypeError:
            return ()

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
        state = self.port.state(context.account)
        snapshot = self.port.snapshot(context.account)
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
            account_state=self.port.state(context.account),
            snapshot=self.port.snapshot(context.account),
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class RuntimeMarketProjectionService:
    data: MarketDataPort

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return tuple(self.data.subscriptions())


@dataclass(frozen=True, slots=True)
class RuntimeMarketService:
    projection: RuntimeMarketProjectionService

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return self.projection.subscriptions()


@dataclass(frozen=True, slots=True)
class RuntimeReferenceProjectionService:
    reference: ReferencePort

    def catalog(self) -> ReferenceCatalog:
        return self.reference.catalog()

    def lifecycle_events(self) -> tuple[LifecycleEvent, ...]:
        return self.reference.lifecycle_events()


@dataclass(frozen=True, slots=True)
class RuntimeReferenceService:
    projection: RuntimeReferenceProjectionService

    def catalog(self) -> ReferenceCatalog:
        return self.projection.catalog()

    def lifecycle_events(self) -> tuple[LifecycleEvent, ...]:
        return self.projection.lifecycle_events()


@dataclass(frozen=True, slots=True)
class RuntimeTradingIntentService:
    execution: TradingExecutionPort

    def execute_intent(self, intent: TradeIntent, context: object, *, hook: str = "") -> object:
        return self.execution.execute_intent(intent, context, hook=hook)


@dataclass(frozen=True, slots=True)
class RuntimeAccountProjectionService:
    account: AccountContext
    coordinator: ExecutionCoordinator
    cash_currency: str = "USD"
    settlement_currency: str = "USD"

    def cash(self, currency: str | None = None) -> Decimal:
        return self.coordinator.ledger.cash(self.account.account).get(currency or self.cash_currency, Decimal("0"))

    def positions(self) -> dict[str, Decimal]:
        return dict(self.coordinator.ledger.positions(self.account.account))

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
                self.account.account,
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
    snapshots: ApplyAccountSnapshotUseCase | None = None
    views: RuntimeAccountViewProjectionService | None = None
    projection: RuntimeAccountProjectionService | None = None

    def apply_snapshot(self, snapshot: AccountSnapshot) -> None:
        if self.snapshots is not None:
            self.snapshots.apply(snapshot)

    def directory(self) -> LaunchAccountDirectory:
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


@dataclass(frozen=True, slots=True)
class RuntimeExecutionService:
    updates: ApplyExecutionUpdateUseCase | None = None
    projection: RuntimeExecutionProjectionService | None = None
    trading_intents: RuntimeTradingIntentService | None = None

    def apply_update(self, update: object) -> OrderState:
        if self.updates is None:
            raise RuntimeError("runtime execution service has no update use case")
        return self.updates.apply(update)  # type: ignore[arg-type]

    def current_view(self) -> ExecutionCurrentView:
        return _require_execution_projection(self.projection).current_view()

    def fills_view(self) -> ExecutionFillsView:
        return _require_execution_projection(self.projection).fills_view()

    def execute_intent(self, intent: TradeIntent, context: object, *, hook: str = "") -> object:
        if self.trading_intents is None:
            raise RuntimeError("runtime execution service has no trading intent executor")
        return self.trading_intents.execute_intent(intent, context, hook=hook)


@dataclass(frozen=True, slots=True)
class RuntimeApplicationServices:
    account: RuntimeAccountService | None = None
    execution: RuntimeExecutionService | None = None
    market: RuntimeMarketService | None = None
    reference: RuntimeReferenceService | None = None

    @classmethod
    def from_dependencies(cls, dependencies: RuntimeServiceDependencies) -> "RuntimeApplicationServices":
        execution_projection = (
            None
            if dependencies.execution is None
            else RuntimeExecutionProjectionService(dependencies.execution, fills_source=dependencies.fills_source)
        )
        account_views = None if dependencies.account is None else RuntimeAccountViewProjectionService(dependencies.account)
        account_projection = _account_projection(dependencies.account, dependencies.execution)
        account_snapshots = ApplyAccountSnapshotUseCase.from_store(dependencies.account_snapshot_store)
        account = (
            None
            if account_views is None and account_projection is None and account_snapshots is None
            else RuntimeAccountService(snapshots=account_snapshots, views=account_views, projection=account_projection)
        )
        execution_updates = (
            None
            if dependencies.execution is None
            else ApplyExecutionUpdateUseCase(dependencies.execution, intents=dependencies.intents)
        )
        trading_intents = (
            None
            if dependencies.trading_execution is None
            else RuntimeTradingIntentService(dependencies.trading_execution)
        )
        execution = (
            None
            if execution_projection is None and execution_updates is None and trading_intents is None
            else RuntimeExecutionService(
                updates=execution_updates,
                projection=execution_projection,
                trading_intents=trading_intents,
            )
        )
        return cls(
            account=account,
            execution=execution,
            market=(
                None
                if dependencies.data is None
                else RuntimeMarketService(RuntimeMarketProjectionService(dependencies.data))
            ),
            reference=(
                None
                if dependencies.reference is None
                else RuntimeReferenceService(RuntimeReferenceProjectionService(dependencies.reference))
            ),
        )


def _require_account_views(service: RuntimeAccountViewProjectionService | None) -> RuntimeAccountViewProjectionService:
    if service is None:
        raise RuntimeError("runtime account service has no view projection")
    return service


def _require_account_projection(service: RuntimeAccountProjectionService | None) -> RuntimeAccountProjectionService:
    if service is None:
        raise RuntimeError("runtime account service has no account projection")
    return service


def _require_execution_projection(service: RuntimeExecutionProjectionService | None) -> RuntimeExecutionProjectionService:
    if service is None:
        raise RuntimeError("runtime execution service has no execution projection")
    return service


def _account_projection(account: AccountPort | None, execution: ExecutionCoordinator | None) -> RuntimeAccountProjectionService | None:
    if account is None or execution is None:
        return None
    accounts = account.accounts()
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


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _execution_order_summary(order: OrderState) -> ExecutionOrderSummary:
    return ExecutionOrderSummary(
        order_id=order.order_id,
        instrument_id=order.request.instrument_id,
        status=order.status.value,
        side=order.request.side.value,
        quantity=order.request.quantity,
        filled_quantity=order.filled_quantity,
        remaining_quantity=order.remaining_quantity,
        market_id=order.request.market_id,
        order_venue_id=order.order_venue_id or order.request.order_venue_id,
        updated_at=order.updated_at,
        reason=order.reason,
    )


def _latest_order(orders: tuple[ExecutionOrderSummary, ...]) -> ExecutionOrderSummary | None:
    if not orders:
        return None
    return max(orders, key=lambda item: item.updated_at or datetime.min)


def _fill_summary(fill: object, *, market_id: str | None) -> ExecutionFillSummary:
    side = getattr(fill, "side", "")
    return ExecutionFillSummary(
        order_id=str(getattr(fill, "order_id", "") or ""),
        intent_id=_optional_text(getattr(fill, "intent_id", None)),
        instrument_id=str(getattr(fill, "instrument_id", "") or ""),
        side=str(getattr(side, "value", side) or ""),
        quantity=getattr(fill, "quantity"),
        price=getattr(fill, "price"),
        fee=getattr(fill, "fee"),
        occurred_at=getattr(fill, "occurred_at"),
        notional=getattr(fill, "notional", None),
        market_id=market_id,
    )


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
    "RuntimeApplicationServices",
    "RuntimeMarketProjectionService",
    "RuntimeMarketService",
    "RuntimeReferenceProjectionService",
    "RuntimeReferenceService",
    "RuntimeExecutionProjectionService",
    "RuntimeExecutionService",
    "RuntimeServiceDependencies",
    "RuntimeTradingIntentService",
]
