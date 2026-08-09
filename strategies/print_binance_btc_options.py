from __future__ import annotations

from kairospy.strategy import StrategyBase


class PrintBinanceBtcOptions(StrategyBase):
    """Subscribe to Binance's BTC option chain and print quotes."""

    strategy_id = "print-binance-btc-options"

    def on_start(self, context) -> None:
        handle = context.subscribe(
            "market.BTC",
            selectors=("quote",),
            exchange="binance",
            market_type="options",
            asset_type="crypto",
            params={"mode": "chain", "underlying": "BTC"},
        )
        for market_id in handle.result.get("added", ()):
            view = context.view(f"market.view.binance.{market_id}.quote")
            if view is None or not view.quotes:
                continue
            quote = view.quotes[0]
            print(
                "Binance BTC option quote "
                f"market={quote.market_id or quote.instrument_id} "
                f"source={quote.source_id or 'unknown'} "
                f"bid={quote.bid_price.value if quote.bid_price else None} "
                f"ask={quote.ask_price.value if quote.ask_price else None}",
                flush=True,
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
