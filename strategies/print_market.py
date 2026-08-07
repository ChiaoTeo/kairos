from kairospy.strategy import StrategyBase


class PrintMarket(StrategyBase):
    strategy_id = "print-market"

    def __init__(self, symbol="BTCUSDT", exchange=None, market_type=None, identity=None):
        self.symbol = str(symbol).upper()
        self.exchange = exchange
        self.market_type = market_type
        self.identity = identity

    def on_start(self, context):
        context.subscribe(
            f"market.{self.symbol}",
            selectors=("quote",),
            exchange=self.exchange,
            market_type=self.market_type,
            identity=self.identity,
        )

    def on_data(self, context, event):
        print(
            f"market event kind={event.kind} payload={event.payload}",
            flush=True,
        )
