from __future__ import annotations

from kairospy.application.strategy import StrategyBase
from kairospy.core.market import Quote


class PrinterStrategy(StrategyBase):
    strategy_id = "printer"

    def __init__(self, instrument: str, venue: str, market: str = "spot") -> None:
        self.instrument = instrument
        self.venue = venue
        self.market = market

    def on_start(self, context) -> None:
        context.subscribe(
            self.instrument,
            venue=self.venue,
            market=self.market,
            selectors=(Quote,),
            identity=self.strategy_id,
        )

    def on_data(self, context, signal) -> None:
        print(f"printer saw {signal.kind} at {signal.time.isoformat()}")


def strategy(
    instrument: str = "BTC/USDT",
    venue: str = "binance",
    market: str = "spot",
) -> PrinterStrategy:
    return PrinterStrategy(instrument=instrument, venue=venue, market=market)
