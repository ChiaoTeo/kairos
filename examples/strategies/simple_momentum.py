from __future__ import annotations

from decimal import Decimal

from kairospy.strategy import StrategyBase, StrategyContext


class SimpleMomentumStrategy(StrategyBase):
    strategy_id = "simple-momentum"

    def __init__(self) -> None:
        self.previous_close: Decimal | None = None
        self.in_position = False

    def on_market(self, context: StrategyContext, event):
        close = Decimal(str(event.payload["close"]))
        previous = self.previous_close
        self.previous_close = close
        if previous is None:
            return ()
        if not self.in_position and close > previous:
            self.in_position = True
            context.target_position(
                "BTC/USDT",
                Decimal("1"),
                reason="close rose above previous bar",
                intent_id=f"enter-{event.sequence}",
            )
            return ()
        if self.in_position and close < previous:
            self.in_position = False
            context.target_position(
                "BTC/USDT",
                Decimal("0"),
                reason="close fell below previous bar",
                intent_id=f"exit-{event.sequence}",
            )
        return ()


__all__ = ["SimpleMomentumStrategy"]
