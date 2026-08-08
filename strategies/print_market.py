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
        if event.kind != "quote":
            return
        payload = event.payload
        print(
            f"market event kind=quote symbol={payload.instrument_id} "
            f"bid={payload.bid_price.value if payload.bid_price else '-'} "
            f"ask={payload.ask_price.value if payload.ask_price else '-'}",
            flush=True,
        )
