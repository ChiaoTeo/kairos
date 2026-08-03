"""A deliberately small strategy for the backtest example."""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from kairospy.application.usecases.strategy.protocol import StrategyBase
from kairospy.domain.intent import target_position_intent
from kairospy.domain.market import Bar
from kairospy.domain.reference import MarketRef


class BtcSmaStrategy(StrategyBase):
    strategy_id = "example-btc-sma"

    def __init__(self, *, fast_window: int = 5, slow_window: int = 20, quantity: str = "0.01") -> None:
        if fast_window < 1 or slow_window <= fast_window:
            raise ValueError("slow_window must be greater than fast_window >= 1")
        self.market = MarketRef.ephemeral(venue="binance", market="spot", source_symbol="BTCUSDT")
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.quantity = Decimal(quantity)
        self.closes: deque[Decimal] = deque(maxlen=slow_window)
        self.in_position = False

    def on_start(self, context) -> None:
        context.subscribe(self.market, selectors=(Bar.select(interval="1m"),), identity=self.strategy_id)

    def on_data(self, context, signal) -> None:
        value = getattr(getattr(signal, "payload", None), "value", None)
        if not isinstance(value, Bar) or value.close is None:
            return None
        self.closes.append(value.close)
        if len(self.closes) < self.slow_window:
            return None
        values = tuple(self.closes)
        fast = sum(values[-self.fast_window :], Decimal("0")) / self.fast_window
        slow = sum(values, Decimal("0")) / self.slow_window
        target = self.quantity if fast > slow else Decimal("0")
        if (target > 0) == self.in_position:
            return None
        self.in_position = target > 0
        context.intent(
            target_position_intent(
                strategy_id=self.strategy_id,
                instrument_id=self.market.instrument_id,
                market_id=self.market.market_id,
                target_quantity=target,
                at=signal.time,
                reason=f"fast_sma={fast} slow_sma={slow}",
            )
        )


def strategy(fast_window: int = 5, slow_window: int = 20, quantity: str = "0.01") -> BtcSmaStrategy:
    return BtcSmaStrategy(fast_window=fast_window, slow_window=slow_window, quantity=quantity)
