from kairospy.strategy import QuoteEvent, Strategy, StrategyContext


class PrintMarket(Strategy):
    strategy_id = "print-market"

    def __init__(self, symbol="BTCUSDT"):
        self.symbol = str(symbol).upper()

    def on_start(self, context: StrategyContext) -> None:
        markets = context.reference.find_markets(symbol=self.symbol)
        if not markets:
            raise RuntimeError(f"no active market found for {self.symbol}")
        for market in markets:
            context.market.subscribe_quotes(market.id)

    def on_quote(self, context: StrategyContext, event: QuoteEvent) -> None:
        del context
        quote = event.data
        print(
            f"market event kind=quote instrument={quote.instrument_id} "
            f"bid={quote.bid_price.value if quote.bid_price is not None else '-'} "
            f"ask={quote.ask_price.value if quote.ask_price is not None else '-'}",
            flush=True,
        )
