from __future__ import annotations

from dataclasses import fields
from decimal import Decimal
from enum import StrEnum
import json
from tempfile import TemporaryDirectory
from typing import Mapping

from kairospy.backtest import (
    BacktestEngine,
    BasisPointSlippageModel,
    ImmediateFillModel,
    PercentageCommissionModel,
    SimulatedAccount,
)
from kairospy.context import DataContext
from kairospy.data import DataStore
from kairospy.runtime import DataViewEventSource
from kairospy.reference import MarketResolver
from kairospy.strategy import StrategyBase, StrategyContext


class BreakoutRoundTripStrategy(StrategyBase):
    strategy_id = "breakout-round-trip"

    def __init__(self) -> None:
        self.previous_close: Decimal | None = None
        self.in_position = False

    def on_market(self, context: StrategyContext, event):
        close = Decimal(str(event.payload["close"]))
        previous = self.previous_close
        self.previous_close = close

        if previous is None:
            return
        if not self.in_position and close > previous:
            self.in_position = True
            context.target_position(
                "BTC/USDT",
                Decimal("1"),
                reason="close broke above previous bar",
                intent_id=f"enter-{event.sequence}",
            )
            return
        if self.in_position and close < previous:
            self.in_position = False
            context.target_position(
                "BTC/USDT",
                Decimal("0"),
                reason="close fell below previous bar",
                intent_id=f"exit-{event.sequence}",
            )


def main() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write(
            "market.ohlcv.simulated_spot_btc_usdt.1m",
            [
                {"time": "2026-01-01T00:00:00+00:00", "market_id": "market:simulated:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "simulated_spot_btc_usdt", "close": 100, "volume": 8},
                {"time": "2026-01-01T00:01:00+00:00", "market_id": "market:simulated:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "simulated_spot_btc_usdt", "close": 103, "volume": 8},
                {"time": "2026-01-01T00:02:00+00:00", "market_id": "market:simulated:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "simulated_spot_btc_usdt", "close": 98, "volume": 8},
                {"time": "2026-01-01T00:03:00+00:00", "market_id": "market:simulated:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "simulated_spot_btc_usdt", "close": 101, "volume": 8},
                {"time": "2026-01-01T00:04:00+00:00", "market_id": "market:simulated:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "simulated_spot_btc_usdt", "close": 106, "volume": 8},
                {"time": "2026-01-01T00:05:00+00:00", "market_id": "market:simulated:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "simulated_spot_btc_usdt", "close": 104, "volume": 8},
            ],
        )
        data = DataContext(
            store,
            markets=MarketResolver(default_venue="simulated", default_market="spot"),
        )
        bars = data.for_market("BTC/USDT").ohlcv("1m")

        engine = BacktestEngine(
            BreakoutRoundTripStrategy(),
            data,
            SimulatedAccount(
                "example-breakout",
                Decimal("1000"),
                cash_currency="USDT",
            ),
            fill_model=ImmediateFillModel(volume_field="volume", participation_rate=Decimal("0.5")),
            slippage_model=BasisPointSlippageModel(Decimal("10")),
            commission_model=PercentageCommissionModel(Decimal("0.001")),
        )
        result = engine.run(DataViewEventSource(bars))

    print(
        json.dumps(
            {
                "account": result.account.value,
                "strategy_id": result.runtime.strategy_id,
                "event_count": result.runtime.event_count,
                "final_equity": result.final_equity,
                "net_profit": result.net_profit,
                "total_return": result.total_return,
                "fills": result.fills,
                "closed_trades": result.trades,
                "metrics": result.metrics,
                "equity_curve": result.equity_curve,
                "intent_states": result.runtime.intent_states,
            },
            indent=2,
            sort_keys=True,
            default=_jsonable,
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
