from __future__ import annotations

from decimal import Decimal

from kairospy.context import StrategyContext
from kairospy.core.market import FIELD_QUOTE_ASK, FIELD_QUOTE_BID
from kairospy.strategy import StrategyBase, StrategySignal


class OneShotLong(StrategyBase):
    strategy_id = "hyperliquid-one-shot-long"

    def __init__(self, symbol: str = "hyperliquid:derivative:BTC/USDC:USDC", quantity: Decimal = Decimal("0.01")) -> None:
        self.symbol = symbol
        self.quantity = quantity
        self.entered = False

    def on_start(self, context: StrategyContext):
        context.subscribe_market_fields(self.symbol, fields=(FIELD_QUOTE_BID, FIELD_QUOTE_ASK))
        return ()

    def on_market(self, context: StrategyContext, signal: StrategySignal):
        if self.entered or not signal.changed("market", "ticker"):
            return ()
        context.target_position(self.symbol, self.quantity, account=0, intent_id="enter")
        self.entered = True
        return ()
