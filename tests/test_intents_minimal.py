from __future__ import annotations

from decimal import Decimal
from tempfile import TemporaryDirectory

from kairospy.context import DataContext, StrategyContext
from kairospy.data import DataStore
from kairospy.core.intent import IntentEvent, IntentEventKind, IntentKind, IntentStatus, TradeIntent
from kairospy.runtime import DataViewEventSource, StrategyRuntime
from kairospy.core.reference import MarketResolver
from kairospy.service.domains.market import bind_market_data
from kairospy.strategy import StrategyBase


class TargetPositionStrategy(StrategyBase):
    strategy_id = "target-position"

    def on_market(self, context: StrategyContext, event):
        active = context.intents.latest(active=True)
        if active is not None:
            return
        context.target_position(
            "BTC/USDT",
            Decimal("0.5"),
            account=0,
            reason="momentum threshold",
            intent_id="intent-1",
        )


class DirectTargetPositionStrategy(StrategyBase):
    strategy_id = "direct-target-position"

    def on_market(self, context: StrategyContext, event):
        active = context.intents.latest(active=True)
        if active is None:
            context.target_position(
                "BTC/USDT",
                Decimal("0.5"),
                reason="momentum threshold",
                intent_id="intent-1",
            )


class ReturnedTradeIntentStrategy(StrategyBase):
    strategy_id = "returned-trade-intent"

    def on_market(self, context: StrategyContext, event):
        return TradeIntent(
            "intent-1",
            context.strategy_id,
            "binance_spot_btc_usdt",
            IntentKind.TARGET_POSITION,
            created_at=context.now,
            target_quantity=Decimal("0.5"),
        )


def test_strategy_runtime_records_typed_trade_intents() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write("market.ohlcv.binance_spot_btc_usdt.1m", [
            {"time": "2026-01-01T00:00:00+00:00", "close": 100},
            {"time": "2026-01-01T00:01:00+00:00", "close": 101},
        ])
        resolver = MarketResolver(default_venue="binance", default_market="spot")
        data = DataContext(store)
        bars = bind_market_data(data, resolver, "BTC/USDT").ohlcv("1m")

        result = StrategyRuntime(TargetPositionStrategy(), data, market_resolver=resolver).run(DataViewEventSource(bars))

        assert len(result.intents) == 1
        assert isinstance(result.intents[0], TradeIntent)
        assert result.intent_states[0].intent.instrument_id == "instrument:spot:btc:usdt"
        assert result.intent_states[0].intent.market_id == "market:binance:spot:btc_usdt"
        assert result.intent_states[0].intent.account_index == 0
        assert result.intent_states[0].intent.account_id is None
        assert result.intent_states[0].intent.target_quantity == Decimal("0.5")
        assert result.intent_states[0].status is IntentStatus.CREATED


def test_strategy_context_target_position_records_intent_without_return_value() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write("market.ohlcv.binance_spot_btc_usdt.1m", [
            {"time": "2026-01-01T00:00:00+00:00", "close": 100},
            {"time": "2026-01-01T00:01:00+00:00", "close": 101},
        ])
        resolver = MarketResolver(default_venue="binance", default_market="spot")
        data = DataContext(store)
        bars = bind_market_data(data, resolver, "BTC/USDT").ohlcv("1m")

        result = StrategyRuntime(DirectTargetPositionStrategy(), data, market_resolver=resolver).run(DataViewEventSource(bars))

        assert len(result.intents) == 1
        assert isinstance(result.intents[0], TradeIntent)
        assert result.intent_states[0].intent.intent_id == "intent-1"
        assert result.intent_states[0].intent.target_quantity == Decimal("0.5")


def test_strategy_runtime_rejects_returned_trade_intents() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write("market.ohlcv.binance_spot_btc_usdt.1m", [
            {"time": "2026-01-01T00:00:00+00:00", "close": 100},
        ])
        resolver = MarketResolver(default_venue="binance", default_market="spot")
        data = DataContext(store)
        bars = bind_market_data(data, resolver, "BTC/USDT").ohlcv("1m")

        try:
            StrategyRuntime(ReturnedTradeIntentStrategy(), data, market_resolver=resolver).run(DataViewEventSource(bars))
        except TypeError as error:
            assert "context.target_position" in str(error)
        else:
            raise AssertionError("returned TradeIntent should not be accepted")


def test_intent_journal_tracks_status_transitions() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write("market.ohlcv.binance_spot_btc_usdt.1m", [
            {"time": "2026-01-01T00:00:00+00:00", "close": 100},
        ])
        resolver = MarketResolver(default_venue="binance", default_market="spot")
        data = DataContext(store)
        bars = bind_market_data(data, resolver, "BTC/USDT").ohlcv("1m")
        runtime = StrategyRuntime(TargetPositionStrategy(), data, market_resolver=resolver)
        result = runtime.run(DataViewEventSource(bars))
        at = result.last_event.time

        planned = runtime.intents.record(IntentEvent("intent-1", IntentEventKind.PLANNED, at, order_ids=("order-1",)))
        ordering = runtime.intents.record(IntentEvent("intent-1", IntentEventKind.ORDERING, at))

        assert planned.order_ids == ("order-1",)
        assert ordering.status is IntentStatus.ORDERING
        assert runtime.intents.latest(instrument_id="instrument:spot:btc:usdt", active=True).intent.intent_id == "intent-1"
