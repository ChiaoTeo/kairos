from __future__ import annotations

from decimal import Decimal

from kairospy.application.strategy import StrategyContext
from kairospy.core.market import Quote
from kairospy.application.strategy import Signal, StrategyBase


class OneShotLong(StrategyBase):
    strategy_id = "hyperliquid-one-shot-long"

    def __init__(self, symbol: str = "hyperliquid:derivative:BTC/USDC:USDC", quantity: Decimal = Decimal("0.01")) -> None:
        self.symbol = symbol
        self.quantity = quantity
        self.entered = False

    def on_start(self, context: StrategyContext):
        context.subscribe_market_data(self.symbol, selectors=(Quote.select("bid", "ask", basis="ticker"),))
        return None

    def on_data(self, context: StrategyContext, signal: Signal):
        if self.entered or not signal.changed("market", "ticker"):
            return None
        context.target_position(self.symbol, self.quantity, account=0, intent_id="enter")
        self.entered = True
        return None
