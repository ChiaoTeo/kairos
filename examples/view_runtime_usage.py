from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from tempfile import TemporaryDirectory
from typing import Any

from kairospy.application.context import DataContext, StrategyContext
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.data import DataStore
from kairospy.application.runtime.kernel import RuntimeKernel
from kairospy.application.runtime.projection.market import MarketCurrentProjection
from kairospy.application.service.domains.market import DataViewEventSource
from kairospy.application.strategy import StrategyBase, StrategySignal, ViewFieldSchema, ViewSchema, ViewStore


@dataclass(frozen=True, slots=True)
class ProjectRegimeView:
    name: str


@dataclass(frozen=True, slots=True)
class StrategyCounterView:
    market_events: int
    regime: str
    last_close: Decimal | None = None


class ViewReadingStrategy(StrategyBase):
    strategy_id = "view-runtime-example"

    def on_start(self, context: StrategyContext):
        regime = context.views.require("project.regime")
        context.views.put("strategy.counter", StrategyCounterView(0, regime.name))
        return ()

    def on_market(self, context: StrategyContext, signal: StrategySignal):
        current = context.views.require("strategy.counter")
        fields = context.views.require("market.fields")
        last_close = _latest_market_field(fields, "Bar.close")
        context.views.put(
            "strategy.counter",
            StrategyCounterView(current.market_events + 1, current.regime, Decimal(str(last_close))),
            as_of=signal.time,
            available_time=signal.time,
        )
        return ()


def main() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write(
            "market.ohlcv.binance.btc_usdt.1m",
            [
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "kind": "bar",
                    "venue": "binance",
                    "market": "spot",
                    "market_id": "market:binance:spot:btc_usdt",
                    "instrument_id": "instrument:spot:btc:usdt",
                    "market_key": "binance_spot_btc_usdt",
                    "timeframe": "1m",
                    "open": "100",
                    "high": "102",
                    "low": "99",
                    "close": "101",
                    "volume": "10",
                },
                {
                    "time": "2026-01-01T00:01:00+00:00",
                    "kind": "bar",
                    "venue": "binance",
                    "market": "spot",
                    "market_id": "market:binance:spot:btc_usdt",
                    "instrument_id": "instrument:spot:btc:usdt",
                    "market_key": "binance_spot_btc_usdt",
                    "timeframe": "1m",
                    "open": "101",
                    "high": "106",
                    "low": "100",
                    "close": "105",
                    "volume": "12",
                },
            ],
        )

        data = DataContext(store)
        bars = data.attach("bars", dataset="market.ohlcv.binance.btc_usdt.1m")

        views = ViewStore()
        views.register(
            ViewSchema(
                "project.regime",
                "project",
                fields=(ViewFieldSchema("name", "project trading regime", "run configuration", "example"),),
                mutability="runtime_writable",
                persistence="checkpointed",
            )
        )
        views.register(
            ViewSchema(
                "strategy.counter",
                "strategy",
                fields=(
                    ViewFieldSchema("market_events", "market events seen by strategy", "runtime sequence", "strategy"),
                    ViewFieldSchema("regime", "project regime copied into strategy state", "run configuration", "project.regime"),
                    ViewFieldSchema("last_close", "latest close read from market.fields", "event time", "market.fields"),
                ),
                mutability="strategy_writable",
                persistence="checkpointed",
            )
        )
        views.put_runtime("project.regime", ProjectRegimeView("risk-on"))

        runtime = RuntimeKernel(
            ViewReadingStrategy(),
            data,
            views=views,
            components=(MarketCurrentProjection(),),
            market_resolver=MarketResolver(default_venue="binance", default_market="spot"),
        )
        result = runtime.run(DataViewEventSource(bars))
        snapshot = runtime.views.snapshot()

        print("strategy:", result.strategy_id)
        print("market events:", result.event_count)
        print("context hash:", snapshot["context_hash"])
        print()

        _print_view("system.strategy", runtime.views.require("system.strategy"))
        _print_view("system.dataflow", runtime.views.require("system.dataflow"))
        _print_view("market.fields", runtime.views.require("market.fields"))
        _print_view("market.bars", runtime.views.require("market.bars"))
        _print_view("market.current", runtime.views.require("market.current"))
        _print_view("strategy.counter", runtime.views.require("strategy.counter"))


def _latest_market_field(view: Any, field: str) -> object:
    for item in reversed(tuple(view.fields)):
        if item.field == field:
            return item.value
    raise RuntimeError(f"missing market field: {field}")


def _print_view(key: str, value: object) -> None:
    print(f"{key}:")
    print(_compact(value))
    print()


def _compact(value: object) -> object:
    if is_dataclass(value):
        return {
            key: _compact(item)
            for key, item in asdict(value).items()
            if item not in (None, (), [], {})
        }
    if isinstance(value, dict):
        return {str(key): _compact(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_compact(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


if __name__ == "__main__":
    main()
