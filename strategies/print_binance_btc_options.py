from __future__ import annotations

from kairospy.strategy import QuoteEvent, Strategy, StrategyContext


class PrintBinanceBtcOptions(Strategy):
    """Subscribe to Binance's BTC option chain and print quotes."""

    strategy_id = "print-binance-btc-options"

    def on_start(self, context: StrategyContext) -> None:
        markets = context.reference.find_markets(
            exchange="binance",
            instrument_kind="option",
        )
        markets = tuple(
            market
            for market in markets
            if (market.base_asset or "").upper() == "BTC"
            or (market.venue_symbol or "").upper().startswith("BTC")
        )
        if not markets:
            raise RuntimeError("no active Binance BTC option markets found")
        for market in markets:
            context.market.subscribe_quotes(market.id)

    def on_quote(self, context: StrategyContext, event: QuoteEvent) -> None:
        del context
        quote = event.data
        print(
            "Binance BTC option quote "
            f"market={quote.scope.market_id} "
            f"provider={quote.provider or 'unknown'} "
            f"bid={quote.bid_price.value if quote.bid_price is not None else '-'} "
            f"ask={quote.ask_price.value if quote.ask_price is not None else '-'}",
            flush=True,
        )
