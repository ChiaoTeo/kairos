from __future__ import annotations

from kairospy.strategy import (
    BarEvent,
    ImmediateAlgorithm,
    InstrumentId,
    MarketId,
    Strategy,
    StrategyContext,
    Quantity,
)


class SpyHourlyBarStrategy(Strategy):
    """Deterministic 1h-Bar example for Massive SPY."""

    strategy_id = "massive-spy-hourly-bar"

    def on_start(self, ctx: StrategyContext) -> None:
        ctx.state.set_int("bar_count", 0)
        market = ctx.reference.require_market(MarketId("market:xnas:equity:SPY"))
        ctx.market.subscribe_bars(market.id, timeframe="1h")

    def on_bar(self, ctx: StrategyContext, event: BarEvent) -> None:
        if event.data.bar_spec_id != "1h":
            return
        count = ctx.state.increment("bar_count")
        if count == 1:
            ctx.execution.target_position(
                InstrumentId(event.data.instrument_id),
                Quantity("1"),
                account="paper-account",
                algorithm=ImmediateAlgorithm(),
                reason="enter after the first completed hourly bar",
            )
        elif count == 3:
            ctx.execution.close_position(
                InstrumentId(event.data.instrument_id),
                account="paper-account",
                algorithm=ImmediateAlgorithm(),
                reason="close after the third completed hourly bar",
            )
