from __future__ import annotations

from kairospy.strategy import StrategyBase


class PrintBinanceBtcOptions(StrategyBase):
    """Subscribe to Binance's BTC option chain and print quotes."""

    strategy_id = "print-binance-btc-options"

    def on_start(self, context) -> None:
        context.subscribe(
            "market.BTC",
            selectors=("quote",),
            exchange="binance",
            market_type="options",
            asset_type="crypto",
            params={"mode": "chain", "underlying": "BTC"},
        )

    def on_data(self, context, event) -> None:
        if event.kind != "quote":
            return
        quote = event.payload
        print(
            "Binance BTC option quote "
            f"market={quote.market_id or quote.instrument_id} "
            f"source={quote.source_id or 'unknown'} "
            f"bid={quote.bid_price.value if quote.bid_price else None} "
            f"ask={quote.ask_price.value if quote.ask_price else None}",
            flush=True,
        )
