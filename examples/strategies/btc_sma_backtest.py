from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal

from kairospy.application.usecases.strategy.protocol import StrategyBase
from kairospy.core.intent import target_position_intent
from kairospy.core.market import Bar
from kairospy.core.reference import MarketRef


@dataclass(frozen=True, slots=True)
class SmaParams:
    fast_window: int = 5
    slow_window: int = 20
    target_quantity: Decimal = Decimal("0.01")


class BtcSmaBacktestStrategy(StrategyBase):
    strategy_id = "btc-sma-backtest"

    def __init__(
        self,
        *,
        fast_window: int = 5,
        slow_window: int = 20,
        target_quantity: str = "0.01",
    ) -> None:
        if fast_window < 1:
            raise ValueError("fast_window must be positive")
        if slow_window <= fast_window:
            raise ValueError("slow_window must be greater than fast_window")
        self.params = SmaParams(fast_window, slow_window, Decimal(target_quantity))
        self.market = MarketRef.ephemeral(venue="binance", market="spot", source_symbol="BTC/USDT")
        self.closes: deque[Decimal] = deque(maxlen=slow_window)
        self.in_position = False

    def on_start(self, context) -> None:
        context.subscribe(
            self.market,
            selectors=(Bar.select(interval="1m"),),
            identity=self.strategy_id,
        )

    def on_data(self, context, signal) -> None:
        bar = _bar(signal)
        if bar is None or bar.close is None:
            return None
        self.closes.append(bar.close)
        if len(self.closes) < self.params.slow_window:
            return None

        values = tuple(self.closes)
        fast = _average(values[-self.params.fast_window :])
        slow = _average(values)
        target = self.params.target_quantity if fast > slow else Decimal("0")
        if (target > 0) == self.in_position:
            return None

        self.in_position = target > 0
        context.trace(
            "sma_cross",
            {
                "action": "target_position",
                "instrument_id": str(self.market.instrument_id),
                "target_quantity": str(target),
                "fast_sma": str(fast),
                "slow_sma": str(slow),
            },
        )
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
        return None


def _bar(signal) -> Bar | None:
    payload = getattr(signal, "payload", None)
    value = getattr(payload, "value", None)
    return value if isinstance(value, Bar) else None


def _average(values: tuple[Decimal, ...]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values))


def strategy(
    fast_window: int = 5,
    slow_window: int = 20,
    target_quantity: str = "0.01",
) -> BtcSmaBacktestStrategy:
    return BtcSmaBacktestStrategy(
        fast_window=fast_window,
        slow_window=slow_window,
        target_quantity=target_quantity,
    )
