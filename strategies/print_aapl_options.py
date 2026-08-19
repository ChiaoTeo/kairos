from __future__ import annotations

from kairospy.strategy import QuoteEvent, Strategy, StrategyContext


class PrintAaplOptions(Strategy):
    """Print Massive AAPL option-chain quotes."""

    strategy_id = "print-aapl-options"

    def on_start(self, context: StrategyContext) -> None:
        markets = context.reference.find_markets(
            exchange="massive",
            instrument_kind="option",
        )
        markets = tuple(
            market
            for market in markets
            if (market.base_asset or "").upper() == "AAPL"
            or (market.venue_symbol or "").upper().startswith("AAPL")
        )
        if not markets:
            raise RuntimeError("no active Massive AAPL option markets found")
        for market in markets:
            context.market.subscribe_quotes(market)

    def on_quote(self, context: StrategyContext, event: QuoteEvent) -> None:
        del context
        quote = event.data
        print(
            "AAPL option quote "
            f"market={quote.market_id} "
            f"source={quote.source_id or 'unknown'} "
            f"bid={quote.bid_price} "
            f"ask={quote.ask_price}",
            flush=True,
        )
