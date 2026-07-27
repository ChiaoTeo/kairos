from __future__ import annotations

from dataclasses import dataclass
from tempfile import TemporaryDirectory

from kairospy.context import DataContext
from kairospy.data import DataStore
from kairospy.runtime import DataViewEventSource, StrategyRuntime
from kairospy.reference import MarketResolver
from kairospy.strategy import Context, ControlRequestKind, StrategyBase, StrategyContext, StrategyRunView, ViewSchema, ViewStore


@dataclass(frozen=True, slots=True)
class ProjectRegimeView:
    name: str


@dataclass(frozen=True, slots=True)
class StrategyCounterView:
    count: int
    regime: str


class ViewMaintainingStrategy(StrategyBase):
    strategy_id = "view-maintainer"

    def on_start(self, context: StrategyContext):
        regime = context.views.require("project.regime")
        context.views.put("strategy.counter", StrategyCounterView(0, regime.name))
        return ()

    def on_market(self, context: StrategyContext, event):
        current = context.views.require("strategy.counter")
        context.views.put("strategy.counter", StrategyCounterView(current.count + 1, current.regime), as_of=event.time)
        return ()

    def on_end(self, context: StrategyContext):
        try:
            context.views.put("system.strategy", StrategyCounterView(99, "bad"))
        except PermissionError:
            return ()
        raise AssertionError("strategy should not be able to write read-only system views")


class ControlRequestingStrategy(StrategyBase):
    strategy_id = "control-requester"

    def on_market(self, context: Context, event):
        context.control.request_subscription("market.orderbook.btc_usdt", reason="need depth", request_id="control-1")
        context.control.request_reduce_only(reason="volatility gate", request_id="control-2")
        return ()


def test_strategy_views_support_project_and_strategy_maintained_views() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write(
            "market.ohlcv.btc_usdt.1m",
            [
                {"time": "2026-01-01T00:00:00+00:00", "close": 100},
                {"time": "2026-01-01T00:01:00+00:00", "close": 101},
            ],
        )
        data = DataContext(store, markets=MarketResolver(default_venue="simulated", default_market="spot"))
        bars = data.attach("bars", dataset="market.ohlcv.btc_usdt.1m")
        views = ViewStore()
        views.register(ViewSchema("project.regime", "project", mutability="runtime_writable", persistence="checkpointed"))
        views.register(ViewSchema("strategy.counter", "strategy", mutability="strategy_writable", persistence="checkpointed"))
        views.put_runtime("project.regime", ProjectRegimeView("risk-on"))

        result = StrategyRuntime(ViewMaintainingStrategy(), data, views=views).run(DataViewEventSource(bars))

        counter = views.require("strategy.counter")
        strategy_run = views.require("system.strategy")
        snapshot = views.snapshot()
        assert result.event_count == 2
        assert counter == StrategyCounterView(2, "risk-on")
        assert isinstance(strategy_run, StrategyRunView)
        assert strategy_run.status == "finished"
        assert snapshot["views"]["strategy.counter"]["payload_hash"]
        assert snapshot["views"]["project.regime"]["owner"] == "project"
        assert snapshot["schemas"]["strategy.counter"]["persistence"] == "checkpointed"


def test_strategy_context_control_records_audited_runtime_requests() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write("market.ohlcv.btc_usdt.1m", [{"time": "2026-01-01T00:00:00+00:00", "close": 100}])
        data = DataContext(store, markets=MarketResolver(default_venue="simulated", default_market="spot"))
        bars = data.attach("bars", dataset="market.ohlcv.btc_usdt.1m")
        runtime = StrategyRuntime(ControlRequestingStrategy(), data)

        result = runtime.run(DataViewEventSource(bars))

        control_view = runtime.views.require("system.control")
        assert len(result.control_requests) == 2
        assert result.control_requests[0].kind is ControlRequestKind.SUBSCRIPTION
        assert result.control_requests[1].kind is ControlRequestKind.REDUCE_ONLY
        assert control_view.total_count == 2
        assert control_view.requests[0].request_id == "control-1"
        assert control_view.requests[1].payload == (("enabled", True),)


def test_runtime_publishes_intent_state_view() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write("market.ohlcv.btc_usdt.1m", [{"time": "2026-01-01T00:00:00+00:00", "close": 100}])
        data = DataContext(store, markets=MarketResolver(default_venue="simulated", default_market="spot"))
        bars = data.attach("bars", dataset="market.ohlcv.btc_usdt.1m")

        class IntentStrategy(StrategyBase):
            strategy_id = "intent-view"

            def on_market(self, context: Context, event):
                context.target_position("btc_usdt", 1, intent_id="intent-1")

        runtime = StrategyRuntime(IntentStrategy(), data)
        runtime.run(DataViewEventSource(bars))

        intent_view = runtime.views.require("system.intents")
        assert intent_view.total_count == 1
        assert intent_view.active_count == 1
        assert intent_view.states[0].intent_id == "intent-1"


def test_strategy_views_require_registered_schema_before_write() -> None:
    views = ViewStore()

    try:
        views.put("strategy.missing", {"value": 1})
    except KeyError as error:
        assert "unknown view schema" in str(error)
    else:
        raise AssertionError("expected missing schema validation")
