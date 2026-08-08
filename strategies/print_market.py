from kairospy.strategy import StrategyBase


class PrintMarket(StrategyBase):
    strategy_id = "print-market"

    def __init__(self, symbol="BTCUSDT"):
        self.symbol = str(symbol).upper()

    def on_start(self, context):
        context.subscribe(
            f"market.{self.symbol}",
            selectors=("quote",),
        )

    def on_data(self, context, event):
        print(
            f"market event kind={event.kind} symbol={event.payload.instrument_id} "
            f"bid={event.payload.bid_price.value if event.payload.bid_price else '-'} "
            f"ask={event.payload.ask_price.value if event.payload.ask_price else '-'}",
            flush=True,
        )
