from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kairospy.contracts import BarPayload
from kairospy.trading.execution import TradeSide
from kairospy.trading.identity import InstrumentId
from kairospy.trading.intent import TargetExposureIntent
from kairospy.execution.intent_status import IntentExecutionTracker, IntentExecutionView
from kairospy.features.runtime import FactorRuntime
from kairospy.market_data.stream import EventSource
from kairospy.risk.portfolio_governance import PositionSizer
from kairospy.strategy.protocols import StrategyContext
from kairospy.strategy.runtime import GovernedStrategyRuntime

from .strategy_run_loop import GovernedStrategyRunLoop, StrategyRunResult


@dataclass(frozen=True, slots=True)
class ImmediateBacktestPortfolio:
    cash: Decimal
    position: Decimal
    mark_price: Decimal

    @property
    def equity(self) -> Decimal:
        return self.cash + self.position * self.mark_price


@dataclass(frozen=True, slots=True)
class ImmediateBacktestTrade:
    timestamp: object
    instrument_id: InstrumentId
    side: TradeSide
    quantity: Decimal
    price: Decimal
    fee: Decimal
    intent_id: object


@dataclass(frozen=True, slots=True)
class ImmediateIntentBacktestResult:
    strategy_run: StrategyRunResult
    trades: tuple[ImmediateBacktestTrade, ...]
    final_portfolio: ImmediateBacktestPortfolio
    intent_executions: tuple[IntentExecutionView, ...]


class _ImmediateExecutionHooks:
    def __init__(self, *, instrument_id: InstrumentId, initial_cash: Decimal, fee_bps: Decimal,
                 lot_size: Decimal, tracker: IntentExecutionTracker) -> None:
        if initial_cash <= 0:
            raise ValueError("immediate backtest requires positive initial cash")
        if fee_bps < 0 or lot_size <= 0:
            raise ValueError("invalid immediate backtest fee or lot size")
        self.instrument_id = instrument_id
        self.initial_cash = initial_cash
        self.approved_capital = initial_cash
        self.fee_rate = fee_bps / Decimal("10000")
        self.lot_size = lot_size
        self.tracker = tracker
        self.cash = initial_cash
        self.position = Decimal("0")
        self.price = Decimal("0")
        self.trades: list[ImmediateBacktestTrade] = []
        self.sizer = PositionSizer()

    @property
    def portfolio(self) -> ImmediateBacktestPortfolio:
        return ImmediateBacktestPortfolio(self.cash, self.position, self.price)

    def before_decision(self, event, market, factor) -> None:
        if not isinstance(event.payload, BarPayload):
            raise TypeError("immediate target backtest requires Bar events")
        self.price = event.payload.close

    def on_intent(self, event, market, factor, economic_intent) -> None:
        for intent in economic_intent.intents:
            if not isinstance(intent, TargetExposureIntent):
                raise TypeError("immediate target backtest supports TargetExposureIntent only")
            capital = max(Decimal("0"), min(self.approved_capital, self.portfolio.equity))
            sized = self.sizer.size(
                intent, approved_capital=capital, reference_price=self.price, lot_size=self.lot_size,
            )
            if not sized.approved or sized.intent is None:
                continue
            target = sized.intent.target_quantity
            delta = target - self.position
            if delta == 0:
                self.tracker.mark_satisfied(intent)
                continue
            side = TradeSide.BUY if delta > 0 else TradeSide.SELL
            quantity = abs(delta)
            notional = quantity * self.price
            fee = notional * self.fee_rate
            if side is TradeSide.BUY:
                affordable = self.cash / (self.price * (Decimal("1") + self.fee_rate))
                quantity = min(quantity, (affordable // self.lot_size) * self.lot_size)
                notional = quantity * self.price
                fee = notional * self.fee_rate
                self.cash -= notional + fee
                self.position += quantity
            else:
                quantity = min(quantity, self.position)
                notional = quantity * self.price
                fee = notional * self.fee_rate
                self.cash += notional - fee
                self.position -= quantity
            if quantity > 0:
                self.trades.append(ImmediateBacktestTrade(
                    event.available_time, self.instrument_id, side, quantity, self.price, fee, intent.intent_id,
                ))
            # Backtest execution is synchronous: after the immediate fill the economic
            # intent is complete for this event. Market realism remains in the chosen
            # price and fee model; asynchronous behavior belongs to historical simulation.
            self.tracker.mark_satisfied(intent, filled_quantity=quantity)

    def on_end(self, context) -> None:
        return None


async def run_immediate_target_backtest(
    *, source: EventSource, factor_runtime: FactorRuntime,
    strategy_runtime: GovernedStrategyRuntime, instrument_id: InstrumentId,
    catalog: object, initial_cash: Decimal, fee_bps: Decimal = Decimal("10"),
    lot_size: Decimal = Decimal("0.0001"),
) -> ImmediateIntentBacktestResult:
    tracker = IntentExecutionTracker(quantity_tolerance=lot_size)
    hooks = _ImmediateExecutionHooks(
        instrument_id=instrument_id, initial_cash=initial_cash, fee_bps=fee_bps,
        lot_size=lot_size, tracker=tracker,
    )
    result = await GovernedStrategyRunLoop(
        source, factor_runtime, strategy_runtime,
        lambda market: StrategyContext(market, hooks.portfolio, (), catalog),
        approved_capital=initial_cash, hooks=hooks, intent_tracker=tracker,
    ).run()
    return ImmediateIntentBacktestResult(result, tuple(hooks.trades), hooks.portfolio, tracker.views)


# The canonical target-intent backtest uses synchronous execution by default.
# Historical simulation is the explicit opt-in path for asynchronous venue behavior.
run_target_backtest = run_immediate_target_backtest
