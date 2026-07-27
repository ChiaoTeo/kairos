from __future__ import annotations

from dataclasses import fields
from decimal import Decimal
from enum import StrEnum
import json
from tempfile import TemporaryDirectory
from typing import Mapping

from kairospy.accounts import (
    Environment,
)
from kairospy.backtest import BacktestEngine, SimulatedAccount
from kairospy.context import DataContext
from kairospy.data import DataStore
from kairospy.runtime import (
    DataViewEventSource,
    RuntimeMode,
)
from kairospy.reference import MarketResolver
from kairospy.paper import PaperEngine
from kairospy.strategy import StrategyBase, StrategyContext


class TwoBarRoundTripStrategy(StrategyBase):
    strategy_id = "same-line-round-trip"

    def __init__(self) -> None:
        self.count = 0

    def on_market(self, context: StrategyContext, event):
        self.count += 1
        if self.count == 1:
            context.target_position("BTC/USDT", Decimal("1"), intent_id=f"enter-{event.sequence}")
        if self.count == 2:
            context.target_position("BTC/USDT", Decimal("0"), intent_id=f"exit-{event.sequence}")


def main() -> None:
    with TemporaryDirectory() as temporary:
        data, bars = _dataset(temporary)

        backtest = BacktestEngine(
            TwoBarRoundTripStrategy(),
            data,
            SimulatedAccount("same-line", Decimal("1000"), cash_currency="USDT"),
        ).run(DataViewEventSource(bars))

        paper = PaperEngine(
            TwoBarRoundTripStrategy(),
            data,
            SimulatedAccount(
                "same-line",
                Decimal("1000"),
                cash_currency="USDT",
                environment=Environment.SIMULATION,
            ),
        ).run(DataViewEventSource(bars))
        paper_view = paper.account_view

    print(
        json.dumps(
            {
                "backtest": {
                    "mode": RuntimeMode.BACKTEST.value,
                    "event_count": backtest.runtime.event_count,
                    "runtime_event_count": backtest.runtime.runtime_event_count,
                    "final_equity": backtest.final_equity,
                    "net_profit": backtest.net_profit,
                    "account_view": backtest.account_view,
                },
                "paper": {
                    "mode": RuntimeMode.PAPER.value,
                    "event_count": paper.runtime.event_count,
                    "runtime_event_count": paper.runtime.runtime_event_count,
                    "final_equity": paper.final_equity,
                    "net_profit": paper.net_profit,
                    "account_view": paper_view,
                },
            },
            indent=2,
            sort_keys=True,
            default=_jsonable,
        )
    )


def _dataset(temporary: str) -> tuple[DataContext, object]:
    store = DataStore(temporary, storage_format="jsonl")
    store.write(
        "market.ohlcv.simulated_spot_btc_usdt.1m",
        [
            {"time": "2026-01-01T00:00:00+00:00", "market_id": "market:simulated:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "simulated_spot_btc_usdt", "close": 100},
            {"time": "2026-01-01T00:01:00+00:00", "market_id": "market:simulated:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "simulated_spot_btc_usdt", "close": 110},
        ],
    )
    data = DataContext(
        store,
        markets=MarketResolver(default_venue="simulated", default_market="spot"),
    )
    return data, data.for_market("BTC/USDT").ohlcv("1m")


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
