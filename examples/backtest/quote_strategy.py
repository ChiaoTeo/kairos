from __future__ import annotations

from decimal import Decimal

from kairospy.strategy import QuoteEvent, Strategy, StrategyContext


class BtcusdtQuoteStrategy(Strategy):
    """Deterministic Quote-driven example for Binance Spot BTCUSDT."""

    strategy_id = "binance-btcusdt-quote"

    def on_start(self, ctx: StrategyContext) -> None:
        ctx.state.set_int("quote_count", 0)
        market = ctx.reference.require_market(
            symbol="BTCUSDT", exchange="binance", market_type="spot"
        )
        ctx.market.subscribe_quotes(market)

    def on_quote(self, ctx: StrategyContext, event: QuoteEvent) -> None:
        quote = event.data
        if quote.ask_price is None or quote.bid_price is None:
            return
        count = ctx.state.increment("quote_count")
        if count == 1:
            ctx.execution.target_position(
                quote.instrument,
                Decimal("0.01"),
                account="paper-account",
                limit_price=quote.ask_price,
                reason="enter on the first executable quote",
            )
        elif count == 2:
            ctx.execution.close_position(
                quote.instrument,
                account="paper-account",
                reason="close on the second quote",
            )
