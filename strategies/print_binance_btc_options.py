from __future__ import annotations

from kairospy.strategy import QuoteEvent, Strategy, StrategyContext


class PrintBinanceBtcOptions(Strategy):
    """Subscribe to Binance's BTC option chain and print quotes."""

    strategy_id = "print-binance-btc-options"

    def on_start(self, context: StrategyContext) -> None:
        markets = context.reference.find_markets(
            exchange="binance",
            market_type="options",
        )
        markets = tuple(
            market
            for market in markets
            if (market.base_asset or "").upper() == "BTC"
            or market.symbol.upper().startswith("BTC")
        )
        if not markets:
            raise RuntimeError("no active Binance BTC option markets found")
        for market in markets:
            context.market.subscribe_quotes(market)

    def on_quote(self, context: StrategyContext, event: QuoteEvent) -> None:
        del context
        quote = event.data
        print(
            "Binance BTC option quote "
            f"market={quote.market_id} "
            f"source={quote.source_id or 'unknown'} "
            f"bid={quote.bid_price} "
            f"ask={quote.ask_price}",
            flush=True,
        )
