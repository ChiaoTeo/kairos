"""Print Massive real-time quotes for one SPY option contract."""

from __future__ import annotations

from kairospy.application.usecases.strategy.protocol import StrategyBase
from kairospy.domain.market import Quote
from kairospy.domain.reference import MarketRef


class MassiveSpyOptionQuoteStrategy(StrategyBase):
    strategy_id = "example-massive-spy-option-quote"

    def __init__(self, contract: str = "O:SPXW260807P07360000") -> None:
        self.market = MarketRef.ephemeral(
            venue="massive", market="option", source_symbol=contract
        )

    def on_start(self, context) -> None:
        context.subscribe(self.market, selectors=(Quote,), identity=self.strategy_id)
        print(f"Subscribed to Massive option quotes: {self.market.source_symbol}", flush=True)

    def on_data(self, context, signal) -> None:
        quote = signal.payload.value
        if isinstance(quote, Quote):
            print(
                f"[{signal.time.isoformat()}] {quote.market_key} "
                f"bid={quote.bid} x {quote.bid_size} "
                f"ask={quote.ask} x {quote.ask_size}",
                flush=True,
            )


def strategy() -> MassiveSpyOptionQuoteStrategy:
    return MassiveSpyOptionQuoteStrategy()


__all__ = ["MassiveSpyOptionQuoteStrategy", "strategy"]
