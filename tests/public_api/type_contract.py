"""Static-only contract checked by Pyright; this is not a pytest module."""

from decimal import Decimal
from typing import assert_type

from kairospy.strategy import (
    AccountExecution,
    SPOT,
    AccountSegmentSnapshot,
    AccountSnapshot,
    Balance,
    Bar,
    BarEvent,
    ImmediateAlgorithm,
    MarketEvent,
    Quote,
    QuoteEvent,
    Strategy,
    StrategyContext,
)


class TypeContractStrategy(Strategy):
    strategy_id = "type-contract"

    def on_start(self, ctx: StrategyContext) -> None:
        assert_type(ctx.account.accounts, tuple[AccountSnapshot, ...])
        spot = ctx.account.account("main").segment(SPOT)
        assert_type(spot, AccountSegmentSnapshot)
        assert_type(spot.balance("USDT"), Balance | None)
        assert_type(spot.require_balance("USDT"), Balance)
        execution = ctx.execution.for_account("main", segment=SPOT)
        assert_type(execution, AccountExecution)

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
                algorithm=ImmediateAlgorithm(),
            )
        elif isinstance(event, QuoteEvent):
            assert_type(event.data, Quote)
            assert_type(event.data.ask_price, Decimal | None)


class TypedMarketHookStrategy(Strategy):
    """The convenience hooks expose concrete event and payload types."""

    strategy_id = "typed-market-hooks"

    def on_bar(self, ctx: StrategyContext, event: BarEvent) -> None:
        assert_type(event.data, Bar)
        assert_type(event.data.close, Decimal)

    def on_quote(self, ctx: StrategyContext, event: QuoteEvent) -> None:
        assert_type(event.data, Quote)
        assert_type(event.data.ask_price, Decimal | None)
