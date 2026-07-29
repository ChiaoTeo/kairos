from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from kairospy.application.system import TradingSystemLauncher


STRATEGY_SOURCE = """
from dataclasses import dataclass
from decimal import Decimal

from kairospy.application.strategy import Signal, StrategyBase, StrategyContext
from kairospy.core.views import ViewFieldSchema, ViewSchema


@dataclass(frozen=True, slots=True)
class StrategyCounterView:
    market_events: int
    regime: str
    last_close: Decimal | None = None


class ViewReadingStrategy(StrategyBase):
    strategy_id = "view-runtime-example"

    def __init__(self, regime: str = "risk-on") -> None:
        self.regime = regime

    def on_start(self, context: StrategyContext):
        context.views.register(
            ViewSchema(
                "strategy.counter",
                "strategy",
                fields=(
                    ViewFieldSchema("market_events", "market events seen by strategy", "runtime sequence", "strategy"),
                    ViewFieldSchema("regime", "strategy regime", "run configuration", "strategy.params"),
                    ViewFieldSchema("last_close", "latest close read from market.fields", "event time", "market.fields"),
                ),
                mutability="strategy_writable",
                persistence="checkpointed",
            )
        )
        context.views.put("strategy.counter", StrategyCounterView(0, self.regime))
        return None

    def on_data(self, context: StrategyContext, signal: Signal):
        current = context.views.require("strategy.counter")
        fields = context.views.require("market.fields")
        last_close = _latest_market_field(fields, "Bar.close")
        context.views.put(
            "strategy.counter",
            StrategyCounterView(current.market_events + 1, current.regime, Decimal(str(last_close))),
            as_of=signal.time,
            available_time=signal.time,
        )
        return None


def _latest_market_field(view, field: str):
    for item in reversed(tuple(view.fields)):
        if item.field == field:
            return item.value
    raise RuntimeError(f"missing market field: {field}")
"""


EVENTS = [
    {
        "time": "2026-01-01T00:00:00+00:00",
        "kind": "bar",
        "venue": "binance",
        "market": "spot",
        "market_id": "market:binance:spot:btc_usdt",
        "instrument_id": "instrument:spot:btc:usdt",
        "market_key": "binance_spot_btc_usdt",
        "source_symbol": "BTC/USDT",
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
        "source_symbol": "BTC/USDT",
        "timeframe": "1m",
        "open": "101",
        "high": "106",
        "low": "100",
        "close": "105",
        "volume": "12",
    },
]


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "strategy_mod.py").write_text(STRATEGY_SOURCE.strip() + "\n", encoding="utf-8")
        (root / "events.jsonl").write_text("\n".join(json.dumps(event) for event in EVENTS) + "\n", encoding="utf-8")
        config_path = root / "backtest.toml"
        config_path.write_text(
            "\n".join(
                [
                    "[run]",
                    'id = "view-runtime-example"',
                    'mode = "backtest"',
                    'strategy = "strategy_mod:ViewReadingStrategy"',
                    "",
                    "[strategy.params]",
                    'regime = "risk-on"',
                    "",
                    "[account]",
                    'cash = "1000"',
                    'currency = "USDT"',
                    "",
                    "[backtest]",
                    'events = "events.jsonl"',
                    'stream = "market.ohlcv.binance.btc_usdt.1m"',
                    'venue = "binance"',
                    'market = "spot"',
                    'price_field = "close"',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        result = TradingSystemLauncher().run_backtest_config(config_path)
        snapshot = result.views.snapshot()

    print("strategy:", result.runtime.strategy_id)
    print("market events:", result.runtime.event_count)
    print("context hash:", snapshot["context_hash"])
    print()

    _print_view("system.strategy", result.views.require("system.strategy"))
    _print_view("system.events", result.views.require("system.events"))
    _print_view("market.fields", result.views.require("market.fields"))
    _print_view("market.bars", result.views.require("market.bars"))
    _print_view("strategy.counter", result.views.require("strategy.counter"))


def _print_view(key: str, value: object) -> None:
    print(f"{key}:")
    print(_compact(value))
    print()


def _compact(value: object) -> object:
    if is_dataclass(value):
        return {key: _compact(item) for key, item in asdict(value).items() if item not in (None, (), [], {})}
    if isinstance(value, dict):
        return {str(key): _compact(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_compact(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


if __name__ == "__main__":
    main()
