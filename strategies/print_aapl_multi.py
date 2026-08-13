from kairospy.strategy import QuoteEvent, Strategy, StrategyContext


class PrintAaplMulti(Strategy):
    strategy_id = "print-aapl-multi"

    def on_start(self, context: StrategyContext) -> None:
        market = context.reference.require_market(
            symbol="AAPL",
            exchange="massive",
            market_type="equity",
        )
        context.market.subscribe_quotes(market)

    def on_market(self, context: StrategyContext, event: QuoteEvent) -> None:
        del context
        if not isinstance(event, QuoteEvent):
            return
        quote = event.data
        source = quote.source_id or str(quote.market_id)
        print(
            f"AAPL quote source={source} "
            f"bid={quote.bid_price if quote.bid_price is not None else '-'} "
            f"ask={quote.ask_price if quote.ask_price is not None else '-'}",
            flush=True,
        )
