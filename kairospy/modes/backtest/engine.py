from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable
from uuid import uuid4

from kairospy.core.account import (
    AccountBalance,
    AccountEvent,
    AccountEventKind,
    AccountSnapshot,
    AccountSource,
    PositionSnapshot,
)
from kairospy.context import DataContext, StrategyContext
from kairospy.core.execution.simulation import (
    CommissionModel,
    FillModel,
    ImmediateFillModel,
    NoSlippageModel,
    PercentageCommissionModel,
    SimulatedExecutionAdapter,
    SimulatedFill,
    SlippageModel,
)
from kairospy.core.intent import TradeIntent
from kairospy.core.order import OrderSide
from kairospy.core.reference import MarketResolver
from kairospy.runtime import (
    EventSource,
    ModeRunner,
    RuntimeDataEnvelope,
    RuntimeMode,
    account_baseline_event,
    account_data_envelope,
)
from kairospy.strategy import Strategy
from kairospy.core.execution import ExecutionCoordinator

from .account import SimulatedAccount
from .metrics import MetricsModel
from .result import BacktestResult, ClosedTrade, EquityPoint


class BacktestEngine:
    runtime_mode = RuntimeMode.BACKTEST

    def __init__(
        self,
        strategy: Strategy,
        data: DataContext,
        account: SimulatedAccount,
        *,
        coordinator: ExecutionCoordinator | None = None,
        fill_model: "FillModel | None" = None,
        slippage_model: "SlippageModel | None" = None,
        commission_model: "CommissionModel | None" = None,
        metrics_model: "MetricsModel | None" = None,
        market_resolver: MarketResolver | None = None,
        account_journal: object | None = None,
    ) -> None:
        self.strategy = strategy
        self.data = data
        self.market_resolver = market_resolver or MarketResolver()
        self.account = account
        self.coordinator = coordinator or ExecutionCoordinator()
        self.fill_model = fill_model or ImmediateFillModel()
        self.slippage_model = slippage_model or NoSlippageModel()
        self.commission_model = commission_model or PercentageCommissionModel(account.fee_rate)
        self.execution_adapter = SimulatedExecutionAdapter(
            account=account.context,
            cash_currency=account.cash_currency,
            price_field=account.price_field,
            coordinator=self.coordinator,
            fill_model=self.fill_model,
            slippage_model=self.slippage_model,
            commission_model=self.commission_model,
        )
        self.metrics_model = metrics_model or MetricsModel()
        self.account_journal = account_journal
        self._marks: dict[str, Decimal] = {}
        self._equity_curve: list[EquityPoint] = []
        self._fills: list[SimulatedFill] = []
        self._trades: list[ClosedTrade] = []
        self._open_trades: dict[str, _OpenTrade] = {}
        self._account_event_sequence = 0

    def run(self, source: EventSource) -> BacktestResult:
        input_events = tuple(source.events())
        first_time = _first_time(input_events)
        self._deposit_initial_cash(first_time)
        runner = ModeRunner(
            self.strategy,
            self.data,
            self.account.context,
            self.runtime_mode,
            equity_currency=self.account.cash_currency,
            initial_equity=self.account.initial_cash,
            market_resolver=self.market_resolver,
        )
        run = runner.run(
            _TupleEventSource(input_events),
            pre_events=(
                account_baseline_event(
                    self.account.context,
                    sequence=self._next_account_event_sequence(),
                    at=first_time,
                    currency=self.account.cash_currency,
                    equity=self.account.initial_cash,
                    source=AccountSource.SIMULATED,
                    metadata={"mode": self.runtime_mode.value},
                ),
            ),
            started_at=first_time,
            intent_handler=self._handle_intents,
        )
        return BacktestResult(
            account=self.account.context,
            initial_equity=self.account.initial_cash,
            runtime=run.runtime,
            equity_curve=tuple(self._equity_curve),
            fills=tuple(self._fills),
            trades=tuple(self._trades),
            metrics=self.metrics_model.evaluate(
                tuple(self._equity_curve),
                tuple(self._trades),
                initial_equity=self.account.initial_cash,
            ),
            coordinator=self.coordinator,
            account_view=run.account_view,
        )

    def _handle_intents(self, intents: tuple[object, ...], context: StrategyContext, hook: str) -> tuple[RuntimeDataEnvelope, ...]:
        self._mark(context)
        for value in intents:
            if isinstance(value, TradeIntent):
                fill = self.execution_adapter.execute_intent(value, context)
                if fill is not None:
                    self._fills.append(fill)
                    journal = getattr(self, "account_journal", None)
                    if journal is not None and hasattr(journal, "record_fill"):
                        journal.record_fill(
                            fill,
                            run_id=None,
                            mode=self.runtime_mode.value,
                        )
                    self._record_trade(fill)
        if hook == "on_market" and context.now is not None:
            return (self._record_equity(context.now),)
        return ()

    def _deposit_initial_cash(self, at: datetime) -> None:
        if self.account.initial_cash == 0:
            return
        self.coordinator.ledger.record(
            AccountEvent(
                uuid4(),
                self.account.context.account,
                AccountEventKind.DEPOSIT,
                at,
                self.account.cash_currency,
                cash_delta=self.account.initial_cash,
            )
        )

    def _record_equity(self, at: datetime) -> RuntimeDataEnvelope:
        account = self.account.context.account
        cash = self.coordinator.ledger.cash(account).get(self.account.cash_currency, Decimal("0"))
        positions = self.coordinator.ledger.positions(account)
        marked_positions = tuple(sorted(positions.items()))
        equity = cash + sum(quantity * self._marks.get(instrument_id, Decimal("0")) for instrument_id, quantity in positions.items())
        self._equity_curve.append(EquityPoint(at, equity, cash, marked_positions))
        point = self._equity_curve[-1]
        journal = getattr(self, "account_journal", None)
        if journal is not None and hasattr(journal, "record_equity_point"):
            journal.record_equity_point(
                point,
                account=self.account.context,
                initial_equity=self.account.initial_cash,
                run_id=None,
                mode=self.runtime_mode.value,
            )
        snapshot = AccountSnapshot(
            self.account.context,
            balances=(
                AccountBalance.from_total_locked(
                    self.account.cash_currency,
                    cash,
                    Decimal("0"),
                    source=AccountSource.SIMULATED,
                ),
            ),
            positions=tuple(
                PositionSnapshot(instrument_id, quantity, AccountSource.SIMULATED)
                for instrument_id, quantity in marked_positions
            ),
            observed_at=at,
            source=AccountSource.SIMULATED,
        )
        projection = self.coordinator.account_projection(self.account.context, venue_snapshot=snapshot)
        return account_data_envelope(
            self.account.context,
            sequence=self._next_account_event_sequence(),
            time=at,
            snapshot=snapshot,
            projection=projection,
            equity=equity,
            source=AccountSource.SIMULATED,
            metadata={
                "mode": self.runtime_mode.value,
                "cash": str(cash),
            },
            stream=f"account.{self.account.context.environment.value}.{account.broker}.{account.account_id}",
        )

    def _record_trade(self, fill: SimulatedFill) -> None:
        current = self._open_trades.get(fill.instrument_id)
        if fill.side is OrderSide.BUY:
            if current is None:
                self._open_trades[fill.instrument_id] = _OpenTrade(
                    fill.instrument_id,
                    fill.occurred_at,
                    fill.quantity,
                    fill.price,
                    fill.fee,
                )
                return
            total_quantity = current.quantity + fill.quantity
            total_cost = current.quantity * current.entry_price + fill.quantity * fill.price
            self._open_trades[fill.instrument_id] = _OpenTrade(
                fill.instrument_id,
                current.opened_at,
                total_quantity,
                total_cost / total_quantity,
                current.fees + fill.fee,
            )
            return

        if current is None:
            return
        close_quantity = min(fill.quantity, current.quantity)
        opening_fee = current.fees * close_quantity / current.quantity
        closing_fee = fill.fee * close_quantity / fill.quantity
        self._trades.append(
            ClosedTrade(
                fill.instrument_id,
                current.opened_at,
                fill.occurred_at,
                close_quantity,
                current.entry_price,
                fill.price,
                (fill.price - current.entry_price) * close_quantity,
                opening_fee + closing_fee,
            )
        )
        remaining = current.quantity - close_quantity
        if remaining == 0:
            del self._open_trades[fill.instrument_id]
            return
        self._open_trades[fill.instrument_id] = _OpenTrade(
            fill.instrument_id,
            current.opened_at,
            remaining,
            current.entry_price,
            current.fees - opening_fee,
        )

    def _mark(self, context: StrategyContext) -> None:
        instrument_id = self._mark_instrument_id(context)
        raw_price = self._mark_price(context, instrument_id=instrument_id)
        if instrument_id is not None and raw_price is not None:
            self._marks[str(instrument_id)] = Decimal(str(raw_price))

    def _mark_instrument_id(self, context: StrategyContext) -> str | None:
        summary = self._latest_price_field(context)
        if summary is None:
            return None
        if getattr(summary, "subject_type", None) == "instrument" and getattr(summary, "subject_id", None) is not None:
            return str(summary.subject_id)
        ref = getattr(summary, "market_id", None) or getattr(summary, "market_key", None) or getattr(summary, "subject_id", None)
        if ref is None:
            return None
        try:
            return self.market_resolver.resolve(ref).instrument_id
        except KeyError:
            return str(ref)

    def _mark_price(self, context: StrategyContext, *, instrument_id: str | None) -> object | None:
        summary = self._latest_price_field(context, instrument_id=instrument_id)
        return None if summary is None else getattr(summary, "value", None)

    def _latest_price_field(self, context: StrategyContext, *, instrument_id: str | None = None) -> object | None:
        fields = context.view("market.fields")
        if fields is None:
            return None
        for item in reversed(tuple(getattr(fields, "fields", ()))):  # projector keeps one latest value per market field key.
            if instrument_id is not None and getattr(item, "subject_id", None) != instrument_id:
                continue
            field = str(getattr(item, "field", ""))
            if field == self.account.price_field or field.endswith(f".{self.account.price_field}"):
                return item
        return None

    def _next_account_event_sequence(self) -> int:
        self._account_event_sequence += 1
        return self._account_event_sequence


def _first_time(events: tuple[RuntimeDataEnvelope, ...]) -> datetime:
    if events:
        return events[0].time
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class _TupleEventSource:
    values: tuple[RuntimeDataEnvelope, ...]

    def events(self) -> Iterable[RuntimeDataEnvelope]:
        return iter(self.values)


@dataclass(frozen=True, slots=True)
class _OpenTrade:
    instrument_id: str
    opened_at: datetime
    quantity: Decimal
    entry_price: Decimal
    fees: Decimal
