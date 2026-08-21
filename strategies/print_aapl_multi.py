from kairospy.strategy import BarEvent, QuoteEvent, Strategy, StrategyContext


class PrintAaplMulti(Strategy):
    strategy_id = "print-aapl-multi"

    def __init__(self, symbol: str = "AAPL") -> None:
        self.symbol = symbol.strip().upper()

    def on_start(self, context: StrategyContext) -> None:
        instruments = context.reference.find_instruments(
            symbol=self.symbol,
            instrument_type="equity",
        )
        if not instruments:
            raise RuntimeError(f"no active equity instrument found for {self.symbol}")
        instrument_ids = {str(instrument.id) for instrument in instruments}
        if len(instrument_ids) != 1:
            raise RuntimeError(
                f"{self.symbol} reference rows do not share one canonical instrument: "
                f"{sorted(instrument_ids)}"
            )
        instrument = instruments[0]
        for source in (
            {
                "source_id": "massive-equity",
                "provider_id": "massive",
                "provider_product": "equity",
                "provider_symbol": self.symbol,
                "network_id": "sip",
            },
            {
                "source_id": "binance-equity",
                "provider_id": "binance",
                "provider_product": "equity",
                "provider_symbol": self.symbol,
                "network_id": None,
            },
        ):
            context.market.subscribe_consolidated_quotes(instrument, **source)
            print(
                f"{self.symbol} subscribed source={source['source_id']} "
                f"instrument={instrument.id} provider={source['provider_id']} "
                f"product={source['provider_product']} symbol={source['provider_symbol']} "
                f"network={source['network_id'] or '*'}",
                flush=True,
            )

    def on_quote(self, context: StrategyContext, event: QuoteEvent) -> None:
        del context
        quote = event.data
        source = quote.source_id or quote.scope.key()
        print(
            f"AAPL quote source={source} "
            f"scope={quote.scope.key()} "
            f"market={quote.market_id or '-'} instrument={quote.instrument.id} "
            f"bid={quote.bid_price if quote.bid_price is not None else '-'} "
            f"ask={quote.ask_price if quote.ask_price is not None else '-'}",
            flush=True,
        )

    def on_bar(self, context: StrategyContext, event: BarEvent) -> None:
        del context
        bar = event.data
        source = bar.source_id or bar.scope.key()
        print(
            f"AAPL bar source={source} "
            f"scope={bar.scope.key()} "
            f"market={bar.market_id or '-'} instrument={bar.instrument.id} "
            f"timeframe={bar.timeframe} close={bar.close}",
            flush=True,
        )
