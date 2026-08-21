from kairospy.strategy import (
    BarEvent,
    MarketData,
    Participant,
    ParticipantSet,
    QuoteEvent,
    Strategy,
    StrategyContext,
    Timeframe,
)


class PrintAaplMulti(Strategy):
    strategy_id = "print-aapl-multi"

    def __init__(self, symbol: str = "AAPL") -> None:
        self.symbol = symbol.strip().upper()

    def on_start(self, context: StrategyContext) -> None:
        markets = context.reference.find_markets(
            symbol=self.symbol,
            instrument_kind="equity",
        )
        if not markets:
            raise RuntimeError(f"no active equity market found for {self.symbol}")
        market_ids = {str(market.id) for market in markets}
        if len(market_ids) != 1:
            raise RuntimeError(
                f"{self.symbol} reference rows do not share one canonical market: "
                f"{sorted(market_ids)}"
            )
        market = markets[0]
        context.market.subscribe(
            market.id,
            data=[MarketData.QUOTE, MarketData.bar(Timeframe.MIN_1)],
            participants=ParticipantSet.only(Participant.MASSIVE, Participant.BINANCE),
        )
        print(
            f"{self.symbol} subscribed market={market.id} "
            "data=quote,bar:1m participants=massive,binance",
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
