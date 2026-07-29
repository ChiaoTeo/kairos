from __future__ import annotations

from decimal import Decimal
from tempfile import TemporaryDirectory

from kairospy.application.runtime import RuntimeMode, RuntimeRunner, RuntimeRunSpec
from kairospy.application.runtime.services.account import account_current_view_key
from kairospy.application.service.domain.account import SimulatedAccount
from kairospy.application.service.domain.market import IterableMarketEventSource, MarketDataResolver
from kairospy.application.service.modes.backtest import (
    BacktestAccountService,
    BacktestExecutionService,
    BacktestMarketDataService,
)
from kairospy.application.strategy import Signal, StrategyBase, StrategyContext
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.data import DataStore


class BuyAndHoldBtc(StrategyBase):
    strategy_id = "buy-and-hold-btc"

    def __init__(self) -> None:
        self.entered = False

    def on_data(self, context: StrategyContext, signal: Signal) -> None:
        if self.entered or not signal.changed("market", "bar"):
            return
        context.target_position("binance:spot:BTC/USDT", Decimal("1"), intent_id="enter-btc")
        self.entered = True


def main() -> None:
    source = IterableMarketEventSource(
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
        account = SimulatedAccount("demo", Decimal("1000"), cash_currency="USDT", broker="backtest")
        coordinator = ExecutionCoordinator()
        account_service = BacktestAccountService(account, coordinator)
        execution_service = BacktestExecutionService(
            coordinator,
            account=account.context,
            cash_currency=account.cash_currency,
            price_field=account.price_field,
        )
        data_service = BacktestMarketDataService(
            DataStore(directory, storage_format="jsonl"),
            resolver=MarketDataResolver(MarketResolver(default_venue="binance", default_market="spot")),
        )
        result = RuntimeRunner.run_sync(
            RuntimeRunSpec(
                run_id="demo",
                mode=RuntimeMode.BACKTEST,
                strategy=BuyAndHoldBtc(),
                source=source,
                data=data_service,
                account=account_service,
                execution=coordinator,
                providers=(execution_service,),
            )
        )
        account_view = result.views.require(account_current_view_key(account.context))

    print("strategy:", result.runtime.strategy_id)
    print("events:", result.runtime.event_count)
    print("intents:", result.runtime.intent_count)
    print("fills:", len(execution_service.fills))
    print("final_equity:", account_view.equity)


if __name__ == "__main__":
    main()
