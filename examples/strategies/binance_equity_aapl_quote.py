"""Continuously print Binance Stocks Trading AAPL bid/ask quotes."""

from __future__ import annotations

from kairospy.application.usecases.strategy.protocol import StrategyBase
from kairospy.domain.market import Quote
from kairospy.domain.reference import MarketRef


class BinanceEquityAaplQuoteStrategy(StrategyBase):
    strategy_id = "example-binance-equity-aapl-quote"

    def __init__(self) -> None:
        self.market = MarketRef.ephemeral(
            venue="binance",
            market="equity",
            source_symbol="AAPL",
        )

    def on_start(self, context) -> None:
        context.subscribe(
            self.market,
            selectors=(Quote,),
            identity=self.strategy_id,
        )
        print("Subscribed to Binance Equity AAPL quotes; waiting for updates...", flush=True)

    def on_data(self, context, signal) -> None:
        event = signal.payload
        quote = event.value
        if not isinstance(quote, Quote):
            return
        print(
            f"[{signal.time.isoformat()}] AAPL "
            f"bid={quote.bid} x {quote.bid_size} "
            f"ask={quote.ask} x {quote.ask_size} "
            f"mid={quote.midpoint}",
            flush=True,
        )


def strategy() -> BinanceEquityAaplQuoteStrategy:
    return BinanceEquityAaplQuoteStrategy()


__all__ = ["BinanceEquityAaplQuoteStrategy", "strategy"]
