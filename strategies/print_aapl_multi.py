from kairospy.strategy import StrategyBase


class PrintAaplMulti(StrategyBase):
    strategy_id = "print-aapl-multi"

    def on_start(self, context):
        for exchange in ("massive", "binance"):
            context.subscribe(
                "market.AAPL",
                selectors=("quote",),
                exchange=exchange,
                market_type="equity",
                asset_type="equity",
            )

    def on_data(self, context, event):
        if event.kind != "quote":
            return
        quote = event.payload
        source = quote.market_id or quote.source_id or "unknown"
        print(
            f"AAPL quote source={source} "
            f"bid={quote.bid_price.value if quote.bid_price else '-'} "
            f"ask={quote.ask_price.value if quote.ask_price else '-'}",
            flush=True,
        )
