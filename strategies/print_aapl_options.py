from __future__ import annotations

from kairospy.strategy import StrategyBase


class PrintAaplOptions(StrategyBase):
    """Print Massive AAPL option-chain quotes."""

    strategy_id = "print-aapl-options"

    def on_start(self, context) -> None:
        context.subscribe(
            "market.AAPL",
            selectors=("quote",),
            exchange="massive",
            market_type="options",
            asset_type="equity",
            params={"mode": "chain", "underlying": "AAPL"},
        )

    def on_data(self, context, event) -> None:
        if event.kind != "quote":
            return
        quote = event.payload
        print(
            "AAPL option quote "
            f"market={quote.market_id or quote.instrument_id} "
            f"source={quote.source_id or 'unknown'} "
            f"bid={quote.bid_price.value if quote.bid_price else None} "
            f"ask={quote.ask_price.value if quote.ask_price else None}",
            flush=True,
        )
