from __future__ import annotations

from decimal import Decimal

from kairospy.strategy import (
    ImmediateAlgorithm,
    InstrumentId,
    QuoteEvent,
    Strategy,
    StrategyContext,
)


class BtcusdtQuoteStrategy(Strategy):
    """Deterministic Quote-driven example for Binance Spot BTCUSDT."""

    strategy_id = "binance-btcusdt-quote"

    def on_start(self, ctx: StrategyContext) -> None:
        ctx.state.set_int("quote_count", 0)
        market = ctx.reference.require_market(
            symbol="BTCUSDT", exchange="binance", instrument_kind="spot"
        )
        ctx.market.subscribe_quotes(market.id)

    def on_quote(self, ctx: StrategyContext, event: QuoteEvent) -> None:
        quote = event.data
        if quote.ask_price is None or quote.bid_price is None:
            return
        count = ctx.state.increment("quote_count")
        if count == 1:
            ctx.execution.target_position(
                InstrumentId(quote.instrument_id),
                Decimal("0.01"),
                account="paper-account",
                algorithm=ImmediateAlgorithm(),
                limit_price=quote.ask_price.value,
                reason="enter on the first executable quote",
            )
        elif count == 2:
            ctx.execution.close_position(
                InstrumentId(quote.instrument_id),
                account="paper-account",
                algorithm=ImmediateAlgorithm(),
                reason="close on the second quote",
            )
