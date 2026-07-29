from __future__ import annotations

import json
from tempfile import TemporaryDirectory
from pathlib import Path

from kairospy.application.system import TradingSystemLauncher


STRATEGY_SOURCE = """
from decimal import Decimal

from kairospy.application.strategy import Signal, StrategyBase, StrategyContext


class BuyAndHoldBtc(StrategyBase):
    strategy_id = "buy-and-hold-btc"

    def __init__(self) -> None:
        self.entered = False

    def on_data(self, context: StrategyContext, signal: Signal) -> None:
        if self.entered or not signal.changed("market", "bar"):
            return
        context.target_position("binance:spot:BTC/USDT", Decimal("1"), intent_id="enter-btc")
        self.entered = True
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
        "high": "101",
        "low": "99",
        "close": "100",
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
        "open": "100",
        "high": "106",
        "low": "100",
        "close": "105",
        "volume": "12",
    },
]


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        strategy_path = root / "strategy_mod.py"
        events_path = root / "events.jsonl"
        config_path = root / "backtest.toml"

        strategy_path.write_text(STRATEGY_SOURCE.strip() + "\n", encoding="utf-8")
        events_path.write_text("\n".join(json.dumps(event) for event in EVENTS) + "\n", encoding="utf-8")
        config_path.write_text(
            "\n".join(
                [
                    "[run]",
                    'id = "demo-backtest"',
                    'mode = "backtest"',
                    'strategy = "strategy_mod:BuyAndHoldBtc"',
                    "",
                    "[account]",
                    'cash = "1000"',
                    'currency = "USDT"',
                    'fee_rate = "0"',
                    "",
                    "[backtest]",
                    'events = "events.jsonl"',
                    'stream = "example.binance.spot.btc_usdt.1m"',
                    'venue = "binance"',
                    'market = "spot"',
                    'price_field = "close"',
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        result = TradingSystemLauncher().run_backtest_config(config_path)

    print("strategy:", result.runtime.strategy_id)
    print("events:", result.runtime.event_count)
    print("intents:", result.runtime.intent_count)
    print("fills:", len(result.fills))
    print("final_equity:", result.final_equity)


if __name__ == "__main__":
    main()
