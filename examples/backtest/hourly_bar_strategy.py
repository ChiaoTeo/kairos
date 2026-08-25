from __future__ import annotations

from decimal import Decimal

from kairospy.strategy import BarEvent, ImmediateAlgorithm, Strategy, StrategyContext


class SpyHourlyBarStrategy(Strategy):
    """Deterministic 1h-Bar example for Massive SPY."""

    strategy_id = "massive-spy-hourly-bar"

    def on_start(self, ctx: StrategyContext) -> None:
        ctx.state.set_int("bar_count", 0)
        market = ctx.reference.require_market(
            symbol="SPY", exchange="massive", instrument_kind="equity"
        )
        ctx.market.subscribe_bars(market, timeframe="1h")

    def on_bar(self, ctx: StrategyContext, event: BarEvent) -> None:
        if event.data.timeframe != "1h":
            return
        count = ctx.state.increment("bar_count")
        if count == 1:
            ctx.execution.target_position(
                event.data.instrument,
                Decimal("1"),
                account="paper-account",
                algorithm=ImmediateAlgorithm(),
                reason="enter after the first completed hourly bar",
            )
        elif count == 3:
            ctx.execution.close_position(
                event.data.instrument,
                account="paper-account",
                algorithm=ImmediateAlgorithm(),
                reason="close after the third completed hourly bar",
            )
