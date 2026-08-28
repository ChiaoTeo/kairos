"""Static-only contract checked by Pyright; this is not a pytest module."""

from typing import assert_type

from kairospy.strategy import (
    AccountExecution,
    AccountEvent,
    SPOT,
    AccountSegmentSnapshot,
    AccountSnapshot,
    Balance,
    Bar,
    BarEvent,
    ExecutionEvent,
    ImmediateAlgorithm,
    InstrumentId,
    MarketEvent,
    PriceLike,
    Quote,
    QuoteEvent,
    RiskEvent,
    Strategy,
    StrategyContext,
    Quantity,
)
from kairospy.primitives.account import AccountIdRead, SegmentKeyRead
from kairospy.primitives.runtime import (
    InstanceIdRead,
    LaunchIdRead,
    StrategyIdRead,
)
from kairospy.primitives.time import SequenceRead, UnixNanosRead


class TypeContractStrategy(Strategy):
    strategy_id = "type-contract"

    def on_start(self, ctx: StrategyContext) -> None:
        assert_type(ctx.strategy_id, StrategyIdRead)
        assert_type(ctx.launch_id, LaunchIdRead)
        assert_type(ctx.instance_id, InstanceIdRead)
        assert_type(ctx.account.accounts, tuple[AccountSnapshot, ...])
        spot = ctx.account.account("main").segment(SPOT)
        assert_type(spot, AccountSegmentSnapshot)
        assert_type(spot.balance("USDT"), Balance | None)
        assert_type(spot.require_balance("USDT"), Balance)
        execution = ctx.execution.for_account("main", segment=SPOT)
        assert_type(execution, AccountExecution)

    def on_market(self, ctx: StrategyContext, event: MarketEvent) -> None:
        if event.kind == "bar_completed":
            bar_event = event
            assert_type(bar_event.data, Bar)
            assert_type(bar_event.data.close, PriceLike)
            count = ctx.state.increment("bar_count")
            assert_type(count, int)
            ctx.execution.target_position(
                InstrumentId(bar_event.data.instrument_id),
                Quantity("1"),
                account="paper-account",
                algorithm=ImmediateAlgorithm(),
            )
        elif event.kind == "quote_updated":
            quote_event = event
            assert_type(quote_event.data, Quote)
            if quote_event.data.ask_price is not None:
                assert_type(quote_event.data.ask_price, PriceLike)

    def on_account(self, ctx: StrategyContext, event: AccountEvent) -> None:
        assert_type(event.account_id, AccountIdRead)
        assert_type(event.metadata.sequence, SequenceRead)
        assert_type(event.metadata.occurred_at_unix_nanos, UnixNanosRead)
        assert_type(event.segment_key, SegmentKeyRead)

    def on_risk(self, ctx: StrategyContext, event: RiskEvent) -> None:
        assert_type(event.metadata.sequence, SequenceRead)

    def on_execution(self, ctx: StrategyContext, event: ExecutionEvent) -> None:
        assert_type(event.metadata.sequence, SequenceRead)


def assert_native_market_payload(event: MarketEvent) -> None:
    assert_type(event.metadata.sequence, SequenceRead)


class TypedMarketHookStrategy(Strategy):
    """The convenience hooks expose concrete event and payload types."""

    strategy_id = "typed-market-hooks"

    def on_bar(self, ctx: StrategyContext, event: BarEvent) -> None:
        assert_type(event.data, Bar)
        assert_type(event.data.close, PriceLike)

    def on_quote(self, ctx: StrategyContext, event: QuoteEvent) -> None:
        assert_type(event.data, Quote)
        if event.data.ask_price is not None:
            assert_type(event.data.ask_price, PriceLike)
