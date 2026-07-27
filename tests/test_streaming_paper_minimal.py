from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from tempfile import TemporaryDirectory

from kairospy.context import DataContext, StrategyContext
from kairospy.core.market import FIELD_QUOTE_ASK, FIELD_QUOTE_BID, MarketSubscription
from kairospy.core.reference import MarketResolver
from kairospy.data import DataStore, InMemoryStreamFeed
from kairospy.modes.backtest import SimulatedAccount
from kairospy.modes.paper import PaperAccountConfig, StreamingPaperEngine, run_streaming_paper
from kairospy.runtime import AsyncDataViewEventSource, AsyncIterableEventSource, StrategyRuntime
from kairospy.strategy import StrategyBase, StrategySignal


class CountTickerStrategy(StrategyBase):
    strategy_id = "count-ticker"

    def __init__(self) -> None:
        self.count = 0

    def on_market(self, context: StrategyContext, signal: StrategySignal):
        if signal.changed("market", "ticker"):
            self.count += 1
        return ()


class OneShotTickerLong(StrategyBase):
    strategy_id = "one-shot-ticker-long"

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.entered = False

    def on_market(self, context: StrategyContext, signal: StrategySignal):
        if self.entered or not signal.changed("market", "ticker"):
            return ()
        context.target_position(self.symbol, Decimal("1"), intent_id="enter")
        self.entered = True
        return ()


class SubscribedOneShotTickerLong(OneShotTickerLong):
    strategy_id = "subscribed-one-shot-ticker-long"

    def __init__(self, symbol: str) -> None:
        super().__init__(symbol)
        self.start_count = 0

    def on_start(self, context: StrategyContext):
        self.start_count += 1
        context.subscribe_market_fields(self.symbol, fields=(FIELD_QUOTE_BID, FIELD_QUOTE_ASK))
        return ()


class DynamicSubscriptionStrategy(SubscribedOneShotTickerLong):
    strategy_id = "dynamic-subscription"

    def on_market(self, context: StrategyContext, signal: StrategySignal):
        context.subscribe_market_fields(
            "ETH/USDC:USDC",
            fields=(FIELD_QUOTE_BID, FIELD_QUOTE_ASK),
            identity="follow-up",
        )
        return super().on_market(context, signal)


def test_async_data_view_event_source_feeds_strategy_runtime() -> None:
    async def scenario() -> None:
        feed = InMemoryStreamFeed()
        with TemporaryDirectory() as directory:
            data = DataContext(DataStore(directory, storage_format="jsonl"), stream_feed=feed)
            view = data.attach("ticker", stream="market.test.btc.ticker", mode="stream")
            strategy = CountTickerStrategy()
            runtime = StrategyRuntime(strategy, data)
            task = asyncio.create_task(runtime.run_async(AsyncDataViewEventSource(view)))
            await asyncio.sleep(0)
            await feed.publish("market.test.btc.ticker", _ticker_row("100", sequence_time="2026-01-01T00:00:00+00:00"))
            await feed.publish("market.test.btc.ticker", _ticker_row("101", sequence_time="2026-01-01T00:00:01+00:00"))
            await feed.close("market.test.btc.ticker")
            result = await task

        assert strategy.count == 2
        assert result.event_count == 2
        assert result.last_event is not None
        assert result.last_event.kind == "ticker"

    asyncio.run(scenario())


def test_streaming_paper_engine_consumes_data_stream_and_simulates_fill() -> None:
    async def scenario() -> None:
        feed = InMemoryStreamFeed()
        with TemporaryDirectory() as directory:
            data = DataContext(DataStore(directory, storage_format="jsonl"), stream_feed=feed)
            view = data.attach("ticker", stream="market.hyperliquid.btc.ticker", mode="stream")
            engine = StreamingPaperEngine(
                OneShotTickerLong("BTC/USDC:USDC"),
                data,
                SimulatedAccount("paper", Decimal("1000"), cash_currency="USDC", price_field="ask"),
                market_resolver=MarketResolver(default_venue="hyperliquid", default_market="derivative"),
            )
            task = asyncio.create_task(engine.run(AsyncDataViewEventSource(view)))
            await asyncio.sleep(0)
            await feed.publish("market.hyperliquid.btc.ticker", _ticker_row("100", sequence_time="2026-01-01T00:00:00+00:00"))
            await feed.close("market.hyperliquid.btc.ticker")
            result = await task

        assert result.runtime.event_count == 1
        assert len(result.runtime.intents) == 1
        assert len(result.fills) == 1
        assert result.fills[0].price == Decimal("101")
        assert result.account_view is not None
        assert result.account_view.equity == Decimal("1000")

    asyncio.run(scenario())


def test_run_streaming_paper_uses_strategy_subscription_controller() -> None:
    async def scenario() -> None:
        strategy = SubscribedOneShotTickerLong("BTC/USDC:USDC")
        controller = FakePaperSourceController()
        result = await run_streaming_paper(
            strategy,
            controller,
            account=PaperAccountConfig(cash="1000", cash_currency="USDC", price_field="ask"),
            market_resolver=MarketResolver(default_venue="hyperliquid", default_market="derivative"),
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        assert strategy.start_count == 1
        assert controller.updates == [("on_start", ("BTC/USDC:USDC",))]
        assert result.runtime.event_count == 1
        assert len(result.runtime.intents) == 1
        assert len(result.fills) == 1
        assert result.fills[0].price == Decimal("101")

    asyncio.run(scenario())


def test_runtime_notifies_subscription_controller_after_later_callbacks() -> None:
    async def scenario() -> None:
        controller = FakePaperSourceController()

        result = await run_streaming_paper(
            DynamicSubscriptionStrategy("BTC/USDC:USDC"),
            controller,
            account=PaperAccountConfig(cash="1000", cash_currency="USDC", price_field="ask"),
            market_resolver=MarketResolver(default_venue="hyperliquid", default_market="derivative"),
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        assert result.runtime.event_count == 1
        assert controller.updates == [
            ("on_start", ("BTC/USDC:USDC",)),
            ("on_market", ("BTC/USDC:USDC", "ETH/USDC:USDC")),
        ]

    asyncio.run(scenario())


def test_streaming_paper_engine_closes_source_when_cancelled() -> None:
    async def scenario() -> None:
        feed = InMemoryStreamFeed()
        closed = asyncio.Event()
        first_event_seen = asyncio.Event()

        class CancellableSource:
            async def events(self):
                try:
                    yield runtime_envelope_from_test_row("100", sequence=1)
                    first_event_seen.set()
                    await asyncio.Event().wait()
                finally:
                    closed.set()

        with TemporaryDirectory() as directory:
            data = DataContext(DataStore(directory, storage_format="jsonl"), stream_feed=feed)
            engine = StreamingPaperEngine(
                OneShotTickerLong("BTC/USDC:USDC"),
                data,
                SimulatedAccount("paper", Decimal("1000"), cash_currency="USDC", price_field="ask"),
                market_resolver=MarketResolver(default_venue="hyperliquid", default_market="derivative"),
            )
            task = asyncio.create_task(engine.run(CancellableSource()))
            await first_event_seen.wait()
            await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert closed.is_set()

    asyncio.run(scenario())


def _ticker_row(quote: str, *, sequence_time: str) -> dict[str, object]:
    return {
        "time": sequence_time,
        "kind": "ticker",
        "market_id": "market:hyperliquid:derivative:btc_usdc_usdc",
        "instrument_id": "instrument:derivative:btc:usdc_usdc",
        "venue": "hyperliquid",
        "market": "derivative",
        "market_key": "hyperliquid_derivative_btc_usdc_usdc",
        "source_symbol": "BTC/USDC:USDC",
        "bid1": quote,
        "ask1": str(Decimal(quote) + Decimal("1")),
    }


def runtime_envelope_from_test_row(quote: str, *, sequence: int):
    from kairospy.runtime import runtime_envelope_from_row

    return runtime_envelope_from_row(
        _ticker_row(quote, sequence_time=f"2026-01-01T00:00:{sequence - 1:02d}+00:00"),
        sequence=sequence,
        stream="market.hyperliquid.btc.ticker",
    )


class FakePaperSourceController:
    def __init__(self) -> None:
        self.updates: list[tuple[str, tuple[str, ...]]] = []
        self._stream = "market.hyperliquid.btc.ticker"

    def update_subscriptions(self, subscriptions: tuple[MarketSubscription, ...], context: StrategyContext, hook: str) -> None:
        self.updates.append((hook, tuple(subscription.source_symbol for subscription in subscriptions)))
        if subscriptions:
            self._stream = subscriptions[0].stream

    def source(self) -> AsyncIterableEventSource:
        return AsyncIterableEventSource(
            self._stream,
            _fake_ticker_rows(),
            limit=1,
        )


async def _fake_ticker_rows():
    yield _ticker_row("100", sequence_time="2026-01-01T00:00:00+00:00")
