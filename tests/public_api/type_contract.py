"""Static-only contract checked by Pyright; this is not a pytest module."""

from decimal import Decimal
from typing import assert_type

from kairospy.strategy import (
    Bar,
    BarEvent,
    MarketEvent,
    Quote,
    QuoteEvent,
    Strategy,
    StrategyContext,
)


class TypeContractStrategy(Strategy):
    strategy_id = "type-contract"

    def on_market(self, ctx: StrategyContext, event: MarketEvent) -> None:
        if event.kind == "bar":
            assert_type(event, BarEvent)
            assert_type(event.data, Bar)
            assert_type(event.data.close, Decimal)
            count = ctx.state.increment("bar_count")
            assert_type(count, int)
            ctx.execution.target_position(
                event.data.instrument,
                Decimal("1"),
                account="paper-account",
            )
        elif isinstance(event, QuoteEvent):
            assert_type(event.data, Quote)
            assert_type(event.data.ask_price, Decimal | None)
