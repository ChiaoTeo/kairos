from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from trading.backtest.execution import combo_quote
from trading.backtest.calendar import TradingCalendar
from trading.domain.execution import TradeSide
from trading.domain.intent import CloseStructureIntent, LegIntent, OpenStructureIntent
from trading.domain.order import Fill, TimeInForce
from trading.domain.product import ListedOptionSpec, OptionRight
from trading.domain.strategy import StrategyContext, StrategyDecision


@dataclass(frozen=True, slots=True)
class BullPutSpreadConfig:
    evaluation_time: time = time(15, 30)
    min_dte: int = 1
    max_dte: int = 7
    target_short_delta: Decimal = Decimal("-0.25")
    width: Decimal = Decimal("50")
    min_credit: Decimal = Decimal("0.50")
    max_leg_spread: Decimal = Decimal("2.00")
    quantity: int = 1
    profit_target: Decimal = Decimal("0.50")
    stop_loss_multiple: Decimal = Decimal("2.00")
    exit_dte: int = 0


class BullPutSpreadStrategy:
    def __init__(self, config: BullPutSpreadConfig = BullPutSpreadConfig(), calendar: TradingCalendar | None = None) -> None:
        self.config = config
        self.calendar = calendar or TradingCalendar()
        self._decisions: list[StrategyDecision] = []
        self._evaluated_dates = set()

    @property
    def strategy_id(self) -> str:
        return "bull-put-spread-v1"

    @property
    def decisions(self) -> tuple[StrategyDecision, ...]:
        return tuple(self._decisions)

    def on_start(self, context: StrategyContext):
        return ()

    def on_market(self, context: StrategyContext):
        if context.working_orders:
            self._record(context, "wait", "working order exists")
            return ()
        structures = [item for item in context.portfolio.open_structures if item.strategy_id == self.strategy_id]
        if structures:
            return self._maybe_close(context, structures[0])
        local_now = context.now.astimezone(self.calendar.timezone)
        local_time = local_now.timetz().replace(tzinfo=None)
        if local_time < self.config.evaluation_time or local_now.date() in self._evaluated_dates:
            return ()
        self._evaluated_dates.add(local_now.date())
        return self._maybe_open(context)

    def _maybe_open(self, context: StrategyContext):
        local_date = context.now.astimezone(self.calendar.timezone).date()
        candidates = []
        for item in context.market.instruments:
            definition = context.catalog.get(item.instrument_id, context.now)
            if not isinstance(definition.product_spec, ListedOptionSpec):
                continue
            option = definition.product_spec
            if option.right is not OptionRight.PUT:
                continue
            dte = self.calendar.dte(local_date, option.expiry.date())
            delta, _ = self._delta(context, item)
            if not self.config.min_dte <= dte <= self.config.max_dte or delta is None:
                continue
            candidates.append((item, option))
        if not candidates:
            self._record(context, "skip", "no eligible put candidates")
            return ()
        short, short_spec = min(candidates, key=lambda pair: abs(self._delta(context, pair[0])[0] - self.config.target_short_delta))
        target_long_strike = short_spec.strike - self.config.width
        long_pair = next((pair for pair in candidates if pair[1].expiry == short_spec.expiry and pair[1].strike == target_long_strike), None)
        keys = tuple(item.instrument_id.value for item, _ in candidates)
        if long_pair is None:
            self._record(context, "skip", "protective long strike unavailable", keys)
            return ()
        long, long_spec = long_pair
        legs = (LegIntent(short.instrument_id, TradeSide.SELL), LegIntent(long.instrument_id, TradeSide.BUY))
        quote = combo_quote(legs, context.market, self.config.quantity)
        if quote is None or quote.max_spread > self.config.max_leg_spread or quote.natural < self.config.min_credit:
            self._record(context, "skip", "credit or liquidity requirement not met", keys)
            return ()
        intent_id = self._id(context, "open")
        _, delta_source = self._delta(context, short)
        self._record(context, "open", f"short={short_spec.strike}, long={long_spec.strike}, natural={quote.natural}, delta_source={delta_source}", keys)
        return (OpenStructureIntent(self.strategy_id, legs, self.config.quantity, self.config.min_credit, TimeInForce.DAY, "scheduled entry", intent_id),)

    def _maybe_close(self, context: StrategyContext, structure):
        legs = tuple(LegIntent(instrument_id, TradeSide.SELL if sign > 0 else TradeSide.BUY, abs(sign)) for instrument_id, sign in structure.legs)
        quote = combo_quote(legs, context.market, structure.quantity)
        if quote is None:
            self._record(context, "hold", "cannot price exit")
            return ()
        close_debit = -quote.natural
        definition = context.catalog.get(structure.legs[0][0], context.now)
        expiry = definition.product_spec.expiry.date() if isinstance(definition.product_spec, ListedOptionSpec) else None
        local_date = context.now.astimezone(self.calendar.timezone).date()
        dte = self.calendar.dte(local_date, expiry) if expiry else 999
        reason = None
        if close_debit <= structure.entry_net_price * (Decimal("1") - self.config.profit_target):
            reason = "profit target"
        elif close_debit >= structure.entry_net_price * self.config.stop_loss_multiple:
            reason = "stop loss"
        elif dte <= self.config.exit_dte:
            reason = "time exit"
        if reason is None:
            self._record(context, "hold", f"close_debit={close_debit}")
            return ()
        self._record(context, "close", f"{reason}; close_debit={close_debit}")
        return (CloseStructureIntent(self.strategy_id, structure.structure_id, legs, structure.quantity, None, TimeInForce.DAY, reason, self._id(context, f"close:{reason}")),)

    def on_fill(self, fill: Fill, context: StrategyContext):
        self._record(context, "fill", f"order={fill.order_id}, net={fill.net_price}")
        return ()

    def on_end(self, context: StrategyContext):
        return ()

    def _id(self, context: StrategyContext, action: str) -> UUID:
        return uuid5(NAMESPACE_URL, f"{self.strategy_id}:{context.now.isoformat()}:{action}")

    def _record(self, context: StrategyContext, action: str, reason: str, candidates=()) -> None:
        self._decisions.append(StrategyDecision(context.now.isoformat(), action, reason, tuple(candidates)))

    @staticmethod
    def _delta(context: StrategyContext, item):
        if context.valuation is not None:
            valuation = context.valuation.get(item.instrument_id)
            if valuation is not None and valuation.pricing is not None:
                return valuation.pricing.delta, valuation.source
        if item.greeks is not None and item.greeks.delta is not None:
            return item.greeks.delta, "vendor"
        return None, "unavailable"
