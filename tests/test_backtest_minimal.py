from __future__ import annotations

from decimal import Decimal
from tempfile import TemporaryDirectory

from kairospy.application.mode.backtest import (
    BacktestEngine,
    BasisPointSlippageModel,
    ImmediateFillModel,
    PercentageCommissionModel,
    SimulatedAccount,
)
from kairospy.application.context import DataContext, StrategyContext
from kairospy.infrastructure.data import DataStore
from kairospy.application.service.domains.market import DataViewEventSource
from kairospy.application.mode.paper import PaperEngine
from kairospy.core.reference import MarketResolver
from kairospy.application.service.domains.market import bind_market_data
from kairospy.application.strategy import StrategyBase


class RoundTripStrategy(StrategyBase):
    strategy_id = "round-trip"

    def __init__(self) -> None:
        self.count = 0

    def on_market(self, context: StrategyContext, event):
        self.count += 1
        if self.count == 1:
            context.target_position(
                "BTC/USDT",
                Decimal("1"),
                intent_id="enter",
            )
        if self.count == 2:
            context.target_position(
                "BTC/USDT",
                Decimal("0"),
                intent_id="exit",
            )


class ThreeBarRoundTripStrategy(StrategyBase):
    strategy_id = "three-bar-round-trip"

    def __init__(self) -> None:
        self.count = 0

    def on_market(self, context: StrategyContext, event):
        self.count += 1
        if self.count == 1:
            context.target_position("BTC/USDT", Decimal("1"), intent_id="enter")
        if self.count == 3:
            context.target_position("BTC/USDT", Decimal("0"), intent_id="exit")


class RestingLimitStrategy(StrategyBase):
    strategy_id = "resting-limit"

    def on_market(self, context: StrategyContext, event):
        context.target_position(
            "BTC/USDT",
            Decimal("1"),
            limit_price=Decimal("90"),
            intent_id="buy-limit",
        )


class AccountAccessorStrategy(StrategyBase):
    strategy_id = "account-accessor"

    def __init__(self) -> None:
        self.seen: list[tuple[str, Decimal | None]] = []

    def on_market(self, context: StrategyContext, event):
        account = context.account()
        self.seen.append((account.context.environment.value, account.equity))
        return ()


def test_backtest_runs_strategy_against_simulated_account_without_strategy_mode_branching() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write(
            "market.ohlcv.binance_spot_btc_usdt.1m",
            [
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "close": 100,
                },
                {
                    "time": "2026-01-01T00:01:00+00:00",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "close": 110,
                },
            ],
        )
        resolver = MarketResolver(default_venue="binance", default_market="spot")
        data = DataContext(store)
        bars = bind_market_data(data, resolver, "BTC/USDT").ohlcv("1m")
        engine = BacktestEngine(
            RoundTripStrategy(),
            data,
            SimulatedAccount("strategy-a", Decimal("1000"), cash_currency="USDT"),
            market_resolver=resolver,
        )

        result = engine.run(DataViewEventSource(bars))

        assert result.account.value == "backtest:simulated:strategy-a"
        assert [point.equity for point in result.equity_curve] == [Decimal("1000"), Decimal("1010")]
        assert result.final_equity == Decimal("1010")
        assert result.net_profit == Decimal("10")
        assert result.total_return == Decimal("0.01")
        assert result.account_view.equity == result.final_equity
        assert result.account_view.net_profit == result.net_profit
        assert result.account_view.total_return == result.total_return
        assert result.account_view.account_state is not None
        assert result.account_view.account_state.balances == result.account_view.balances
        assert result.account_view.pending_orders == ()
        assert [state.status.value for state in result.runtime.intent_states] == ["satisfied", "satisfied"]


def test_strategy_context_account_accessor_is_shared_by_backtest_and_paper() -> None:
    with TemporaryDirectory() as temporary:
        data, bars, resolver = _bars(
            temporary,
            [
                ("2026-01-01T00:00:00+00:00", 100),
                ("2026-01-01T00:01:00+00:00", 110),
            ],
        )
        backtest_strategy = AccountAccessorStrategy()
        paper_strategy = AccountAccessorStrategy()

        backtest = BacktestEngine(
            backtest_strategy,
            data,
            SimulatedAccount("strategy-a", Decimal("1000"), cash_currency="USDT"),
            market_resolver=resolver,
        )
        paper = PaperEngine(
            paper_strategy,
            data,
            SimulatedAccount("strategy-a", Decimal("1000"), cash_currency="USDT"),
            market_resolver=resolver,
        )

        backtest.run(DataViewEventSource(bars))
        paper.run(DataViewEventSource(bars))

        assert backtest_strategy.seen == [("backtest", Decimal("1000")), ("backtest", Decimal("1000"))]
        assert paper_strategy.seen == [("paper", Decimal("1000")), ("paper", Decimal("1000"))]


def test_backtest_applies_slippage_and_commission_to_fills_and_closed_trades() -> None:
    with TemporaryDirectory() as temporary:
        data, bars, resolver = _bars(
            temporary,
            [
                ("2026-01-01T00:00:00+00:00", 100),
                ("2026-01-01T00:01:00+00:00", 110),
            ],
        )
        engine = BacktestEngine(
            RoundTripStrategy(),
            data,
            SimulatedAccount("strategy-a", Decimal("1000"), cash_currency="USDT"),
            slippage_model=BasisPointSlippageModel(Decimal("100")),
            commission_model=PercentageCommissionModel(Decimal("0.01")),
            market_resolver=resolver,
        )

        result = engine.run(DataViewEventSource(bars))

        assert [fill.price for fill in result.fills] == [Decimal("101.00"), Decimal("108.90")]
        assert [fill.fee for fill in result.fills] == [Decimal("1.0100"), Decimal("1.0890")]
        assert result.final_equity == Decimal("1005.8010")
        assert result.net_profit == Decimal("5.8010")
        assert len(result.trades) == 1
        assert result.trades[0].gross_pnl == Decimal("7.90")
        assert result.trades[0].fees == Decimal("2.0990")
        assert result.trades[0].net_pnl == Decimal("5.8010")
        assert result.metrics.trade_count == 1
        assert result.metrics.win_count == 1
        assert result.metrics.win_rate == Decimal("1")


def test_backtest_metrics_include_drawdown_and_sharpe_from_equity_curve() -> None:
    with TemporaryDirectory() as temporary:
        data, bars, resolver = _bars(
            temporary,
            [
                ("2026-01-01T00:00:00+00:00", 100),
                ("2026-01-01T00:01:00+00:00", 90),
                ("2026-01-01T00:02:00+00:00", 110),
            ],
        )
        engine = BacktestEngine(
            ThreeBarRoundTripStrategy(),
            data,
            SimulatedAccount("strategy-a", Decimal("1000"), cash_currency="USDT"),
            market_resolver=resolver,
        )

        result = engine.run(DataViewEventSource(bars))

        assert [point.equity for point in result.equity_curve] == [
            Decimal("1000"),
            Decimal("990"),
            Decimal("1010"),
        ]
        assert result.metrics.net_profit == Decimal("10")
        assert result.metrics.max_drawdown == Decimal("10")
        assert result.metrics.max_drawdown_pct == Decimal("0.01")
        assert result.metrics.sharpe > 0


def test_backtest_fill_model_rejects_uncrossed_limit_order() -> None:
    with TemporaryDirectory() as temporary:
        data, bars, resolver = _bars(temporary, [("2026-01-01T00:00:00+00:00", 100)])
        engine = BacktestEngine(
            RestingLimitStrategy(),
            data,
            SimulatedAccount("strategy-a", Decimal("1000"), cash_currency="USDT"),
            market_resolver=resolver,
        )

        result = engine.run(DataViewEventSource(bars))

        assert result.fills == ()
        assert result.final_equity == Decimal("1000")
        assert [state.status.value for state in result.runtime.intent_states] == ["rejected"]


def test_backtest_fill_model_can_partially_fill_with_volume_participation() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write(
            "market.ohlcv.binance_spot_btc_usdt.1m",
            [
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "close": 100,
                    "volume": 1,
                },
            ],
        )
        resolver = MarketResolver(default_venue="binance", default_market="spot")
        data = DataContext(store)
        bars = bind_market_data(data, resolver, "BTC/USDT").ohlcv("1m")

        class LargeTargetStrategy(StrategyBase):
            strategy_id = "large-target"

            def on_market(self, context: StrategyContext, event):
                context.target_position("BTC/USDT", Decimal("2"), intent_id="enter")

        engine = BacktestEngine(
            LargeTargetStrategy(),
            data,
            SimulatedAccount("strategy-a", Decimal("1000"), cash_currency="USDT"),
            fill_model=ImmediateFillModel(volume_field="volume"),
            market_resolver=resolver,
        )

        result = engine.run(DataViewEventSource(bars))

        assert [fill.quantity for fill in result.fills] == [Decimal("1")]
        assert [point.equity for point in result.equity_curve] == [Decimal("1000")]
        assert [order.status.value for order in result.account_view.pending_orders] == ["partially_filled"]
        assert [order.remaining_quantity for order in result.account_view.pending_orders] == [Decimal("1")]
        assert [state.status.value for state in result.runtime.intent_states] == ["partially_filled"]


def _bars(temporary: str, values: list[tuple[str, int]]) -> tuple[DataContext, object, MarketResolver]:
    store = DataStore(temporary, storage_format="jsonl")
    store.write(
        "market.ohlcv.binance_spot_btc_usdt.1m",
        [
            {
                "time": at,
                "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                "close": close,
            }
            for at, close in values
        ],
    )
    resolver = MarketResolver(default_venue="binance", default_market="spot")
    data = DataContext(store)
    return data, bind_market_data(data, resolver, "BTC/USDT").ohlcv("1m"), resolver
