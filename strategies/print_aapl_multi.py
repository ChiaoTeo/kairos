from kairospy.strategy import (
    BarEvent,
    MarketData,
    Provider,
    ProviderPreference,
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
            provider_preference=ProviderPreference.require(
                Provider.MASSIVE, Provider.BINANCE
            ),
        )
        print(
            f"{self.symbol} subscribed market={market.id} "
            "data=quote,bar:1m providers=massive,binance",
            flush=True,
        )

    def on_quote(self, context: StrategyContext, event: QuoteEvent) -> None:
        del context
        quote = event.data
        scope_key = _scope_key(quote.scope)
        provider = quote.provider or scope_key
        print(
            f"AAPL quote provider={provider} "
            f"scope={scope_key} "
            f"market={quote.scope.market_id or '-'} instrument={quote.instrument_id} "
            f"bid={quote.bid_price.value if quote.bid_price is not None else '-'} "
            f"ask={quote.ask_price.value if quote.ask_price is not None else '-'}",
            flush=True,
        )

    def on_bar(self, context: StrategyContext, event: BarEvent) -> None:
        del context
        bar = event.data
        scope_key = _scope_key(bar.scope)
        provider = bar.provider or scope_key
        print(
            f"AAPL bar provider={provider} "
            f"scope={scope_key} "
            f"market={bar.scope.market_id or '-'} instrument={bar.instrument_id} "
            f"timeframe={bar.bar_spec_id} close={bar.close.value}",
            flush=True,
        )


def _scope_key(scope: object) -> str:
    market_id = getattr(scope, "market_id", None)
    if market_id is not None:
        return str(market_id)
    return (
        f"consolidated:{getattr(scope, 'instrument_id')}:"
        f"{getattr(scope, 'network_id', None) or '*'}"
    )
