from __future__ import annotations

from decimal import Decimal
from tempfile import TemporaryDirectory

from kairospy.application.context import DataContext, StrategyContext
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.data import DataStore
from kairospy.application.mode.backtest import BacktestEngine, SimulatedAccount
from kairospy.application.service.domains.market import IterableEventSource
from kairospy.application.strategy import StrategyBase, StrategySignal


class BuyAndHoldBtc(StrategyBase):
    strategy_id = "buy-and-hold-btc"

    def __init__(self) -> None:
        self.entered = False

    def on_market(self, context: StrategyContext, signal: StrategySignal):
        if self.entered or not signal.changed("market", "bar"):
            return ()
        context.target_position("BTC/USDT", Decimal("1"), intent_id="enter-btc")
        self.entered = True
        return ()


def main() -> None:
    source = IterableEventSource(
        "example.binance.spot.btc_usdt.1m",
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
                "timeframe": "1m",
                "open": "100",
                "high": "106",
                "low": "100",
                "close": "105",
                "volume": "12",
            },
        ],
    )

    with TemporaryDirectory() as directory:
        result = BacktestEngine(
            BuyAndHoldBtc(),
            DataContext(DataStore(directory, storage_format="jsonl")),
            SimulatedAccount("demo", Decimal("1000"), cash_currency="USDT"),
            market_resolver=MarketResolver(default_venue="binance", default_market="spot"),
        ).run(source)

    print("strategy:", result.runtime.strategy_id)
    print("events:", result.runtime.event_count)
    print("intents:", len(result.runtime.intents))
    print("fills:", len(result.fills))
    print("final_equity:", result.equity_curve[-1].equity if result.equity_curve else None)


if __name__ == "__main__":
    main()
