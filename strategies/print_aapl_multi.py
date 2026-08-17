from kairospy.strategy import BarEvent, QuoteEvent, Strategy, StrategyContext


class PrintAaplMulti(Strategy):
    strategy_id = "print-aapl-multi"

    def on_start(self, context: StrategyContext) -> None:
        markets = context.reference.find_markets(
            symbol="AAPL",
            market_type="equity",
        )
        if len(markets) < 2:
            raise RuntimeError(
                f"AAPL multi-source validation requires at least two markets; found {len(markets)}"
            )
        instrument_ids = {str(market.instrument.id) for market in markets}
        if len(instrument_ids) != 1:
            raise RuntimeError(
                f"AAPL markets do not share one canonical instrument: {sorted(instrument_ids)}"
            )
        for market in markets:
            context.market.subscribe_quotes(market)
            print(
                f"AAPL subscribed market={market.id} "
                f"instrument={market.instrument.id} exchange={market.exchange_id}",
                flush=True,
            )

    def on_quote(self, context: StrategyContext, event: QuoteEvent) -> None:
        del context
        quote = event.data
        source = quote.source_id or str(quote.market_id)
        print(
            f"AAPL quote source={source} "
            f"market={quote.market_id} instrument={quote.instrument.id} "
            f"bid={quote.bid_price if quote.bid_price is not None else '-'} "
            f"ask={quote.ask_price if quote.ask_price is not None else '-'}",
            flush=True,
        )

    def on_bar(self, context: StrategyContext, event: BarEvent) -> None:
        del context
        bar = event.data
        source = bar.source_id or str(bar.market_id)
        print(
            f"AAPL bar source={source} "
            f"market={bar.market_id} instrument={bar.instrument.id} "
            f"timeframe={bar.timeframe} close={bar.close}",
            flush=True,
        )
