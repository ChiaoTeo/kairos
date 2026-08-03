"""Buy BTC on the first trade event and exit after a fixed hold time."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from kairospy.application.usecases.strategy.protocol import StrategyBase
from kairospy.domain.market import TradePrint
from kairospy.domain.reference import MarketRef


class BinanceBtcHoldStrategy(StrategyBase):
    strategy_id = "example-binance-btc-hold"

    def __init__(self, *, quantity: str = "0.001", hold_minutes: int = 10) -> None:
        if Decimal(quantity) <= 0:
            raise ValueError("quantity must be positive")
        if hold_minutes < 1:
            raise ValueError("hold_minutes must be positive")
        self.market = MarketRef.ephemeral(venue="binance", market="spot", source_symbol="BTCUSDT")
        self.quantity = Decimal(quantity)
        self.hold_for = timedelta(minutes=hold_minutes)
        self.entry_time = None
        self.exited = False

    def on_start(self, context) -> None:
        context.subscribe(self.market, selectors=(TradePrint,), identity=self.strategy_id)

    def on_data(self, context, signal) -> None:
        if self.exited:
            return None
        event_time = signal.time
        if self.entry_time is None:
            self.entry_time = event_time
            context.target_position(
                self.market,
                self.quantity,
                reason=f"buy BTC and hold for {self.hold_for}",
            )
            context.trace(
                "btc_hold_entry",
                {"symbol": "BTCUSDT", "quantity": str(self.quantity), "entry_time": event_time},
            )
            return None
        if event_time - self.entry_time < self.hold_for:
            return None
        self.exited = True
        context.target_position(
            self.market,
            Decimal("0"),
            reason=f"sell BTC after holding for {self.hold_for}",
        )
        context.trace(
            "btc_hold_exit",
            {"symbol": "BTCUSDT", "quantity": "0", "exit_time": event_time},
        )


def strategy(quantity: str = "0.001", hold_minutes: int = 10) -> BinanceBtcHoldStrategy:
    return BinanceBtcHoldStrategy(quantity=quantity, hold_minutes=hold_minutes)
