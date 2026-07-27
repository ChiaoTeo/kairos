from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal
from enum import StrEnum
import json
from tempfile import TemporaryDirectory
from typing import Mapping

from kairospy.context import DataContext
from kairospy.data import DataStore
from kairospy.runtime import DataViewEventSource, MarketCurrentProjection, StrategyRuntime
from kairospy.reference import MarketResolver
from kairospy.strategy import Context, StrategyBase, ViewSchema, ViewStore


@dataclass(frozen=True, slots=True)
class ProjectRegimeView:
    name: str
    max_position: int


@dataclass(frozen=True, slots=True)
class StrategyCounterView:
    event_count: int
    last_close: float | None
    regime: str


class ViewDemoStrategy(StrategyBase):
    strategy_id = "view-demo"

    def on_start(self, context: Context):
        regime = context.views.require("project.regime")
        context.views.put(
            "strategy.counter",
            StrategyCounterView(event_count=0, last_close=None, regime=regime.name),
        )
        return ({"type": "started", "regime": regime.name},)

    def on_market(self, context: Context, event):
        counter = context.views.require("strategy.counter")
        market = context.views.require("market.current")
        close = float(event.payload["close"])
        next_count = counter.event_count + 1
        context.views.put(
            "strategy.counter",
            StrategyCounterView(event_count=next_count, last_close=close, regime=counter.regime),
            as_of=event.time,
            available_time=event.time,
        )
        outputs: list[object] = [
            {
                "type": "seen_market",
                "event_count": next_count,
                "component_event_count": market.event_count,
                "close": close,
            }
        ]
        if close >= 101:
            context.control.request_reduce_only(
                reason="example volatility gate",
                request_id="control-reduce-only",
            )
        if next_count == 1:
            context.target_position(
                "BTC/USDT",
                1,
                reason="example target",
                intent_id="intent-demo",
            )
        return tuple(outputs)

    def on_end(self, context: Context):
        counter = context.views.require("strategy.counter")
        control = context.views.require("system.control")
        intents = context.views.require("system.intents")
        return (
            {
                "type": "finished",
                "events": counter.event_count,
                "control_requests_visible": control.total_count,
                "intent_states_visible": intents.total_count,
            },
        )


def main() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write(
            "market.ohlcv.simulated_spot_btc_usdt.1m",
            [
                {"time": "2026-01-01T00:00:00+00:00", "close": 100.0},
                {"time": "2026-01-01T00:01:00+00:00", "close": 101.2},
            ],
        )
        data = DataContext(
            store,
            markets=MarketResolver(default_venue="simulated", default_market="spot"),
        )
        bars = data.for_market("BTC/USDT").ohlcv("1m")
        views = ViewStore()
        views.register(ViewSchema("project.regime", "project", mutability="runtime_writable", persistence="checkpointed"))
        views.register(ViewSchema("strategy.counter", "strategy", mutability="strategy_writable", persistence="checkpointed"))
        views.put_runtime("project.regime", ProjectRegimeView(name="risk-on", max_position=3))
        runtime = StrategyRuntime(
            ViewDemoStrategy(),
            data,
            views=views,
            components=(MarketCurrentProjection(),),
        )
        result = runtime.run(DataViewEventSource(bars))

    counter = runtime.views.require("strategy.counter")
    market = runtime.views.require("market.current")
    control = runtime.views.require("system.control")
    intents = runtime.views.require("system.intents")
    strategy_run = runtime.views.require("system.strategy")
    print(
        json.dumps(
            {
                "strategy_id": result.strategy_id,
                "event_count": result.event_count,
                "callback_intents": [_jsonable(item) for item in result.intents],
                "project_regime": _jsonable(runtime.views.require("project.regime")),
                "strategy_counter": _jsonable(counter),
                "market_current": _jsonable(market),
                "system_strategy": _jsonable(strategy_run),
                "system_control": _jsonable(control),
                "system_intents": _jsonable(intents),
                "view_keys": sorted(runtime.views.envelopes().keys()),
            },
            indent=2,
            sort_keys=True,
        )
    )


def _jsonable(value: object) -> object:
    if hasattr(value, "__dataclass_fields__"):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


if __name__ == "__main__":
    main()
