from __future__ import annotations

from decimal import Decimal

from kairospy.context import StrategyContext
from kairospy.core.market import FIELD_BAR_CLOSE, MarketDataField
from kairospy.strategy import StrategyBase, StrategySignal


class SmaCrossBacktest(StrategyBase):
    strategy_id = "sma-cross-backtest"

    def __init__(
        self,
        symbol: str,
        quantity: str | Decimal,
        fast_window: int = 3,
        slow_window: int = 5,
        venue: str | None = None,
        market: str | None = None,
        timeframe: str = "1m",
    ) -> None:
        if fast_window < 1 or slow_window <= fast_window:
            raise ValueError("SMA windows must satisfy 1 <= fast_window < slow_window")
        self.symbol = symbol
        self.quantity = Decimal(str(quantity))
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.venue = venue
        self.market = market
        self.timeframe = timeframe
        self.closes: list[Decimal] = []
        self.positioned = False

    def on_start(self, context: StrategyContext):
        context.subscribe_market_fields(
            self.symbol,
            venue=self.venue,
            market=self.market,
            fields=(MarketDataField(FIELD_BAR_CLOSE, interval=self.timeframe),),
        )
        return ()

    def on_market(self, context: StrategyContext, signal: StrategySignal):
        if not signal.changed("market", "bar"):
            return ()
        close = _latest_close(context)
        if close is None:
            return ()
        self.closes.append(close)
        if len(self.closes) < self.slow_window:
            return ()

        fast = _mean(self.closes[-self.fast_window:])
        slow = _mean(self.closes[-self.slow_window:])
        if fast > slow and not self.positioned:
            self.positioned = True
            context.target_position(self.symbol, self.quantity, intent_id=f"long-{len(self.closes)}")
        elif fast <= slow and self.positioned:
            self.positioned = False
            context.target_position(self.symbol, Decimal("0"), intent_id=f"flat-{len(self.closes)}")
        return ()


def _latest_close(context: StrategyContext) -> Decimal | None:
    fields = context.views.require("market.fields")
    for item in reversed(tuple(getattr(fields, "fields", ()))):  # latest field summaries are stored by market field key.
        if getattr(item, "field", None) == FIELD_BAR_CLOSE:
            value = getattr(item, "value", None)
            return None if value is None else Decimal(str(value))
    return None


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values))
