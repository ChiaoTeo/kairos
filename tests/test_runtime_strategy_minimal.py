from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
import json
from tempfile import TemporaryDirectory
import runpy

import pytest

from kairospy.accounts import AccountBalance, AccountContext, AccountRef, AccountSnapshot, AccountSource, Environment
from kairospy.context import DataContext
from kairospy.data import DataStore
from kairospy.runtime import (
    AccountCurrentProjection,
    AccountRuntimeEvent,
    ClockEvent,
    DataViewEventSource,
    IterableEventSource,
    MarketCurrentProjection,
    StrategyRuntime,
    SystemEventProjection,
    SystemRuntimeEvent,
)
from kairospy.strategy import StrategyBase, StrategyContext


class RecordingStrategy(StrategyBase):
    strategy_id = "recording"

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def on_start(self, context: StrategyContext):
        self.calls.append(("start", context.now))
        return ({"kind": "started"},)

    def on_market(self, context: StrategyContext, event):
        self.calls.append(("market", event.payload["close"]))
        assert context.now == event.time
        assert context.stream == "bars"
        return ({"kind": "seen", "close": event.payload["close"]},)

    def on_end(self, context: StrategyContext):
        self.calls.append(("end", context.now))
        return ({"kind": "ended"},)


class ViewReadingStrategy(StrategyBase):
    strategy_id = "view-reader"

    def __init__(self) -> None:
        self.seen: list[tuple[int, object]] = []

    def on_market(self, context: StrategyContext, event):
        market = context.views.require("market.current")
        self.seen.append((market.event_count, market.last_payload["close"]))
        return ()


class QuoteReadingStrategy(StrategyBase):
    strategy_id = "quote-reader"

    def __init__(self) -> None:
        self.subscription = None
        self.seen = []

    def on_start(self, context: StrategyContext):
        self.subscription = context.subscribe_quote("BTC/USDT", venue="binance", market="spot")
        return ()

    def on_market(self, context: StrategyContext, event):
        quote = context.market.latest_quote("BTC/USDT", venue="binance", market="spot")
        self.seen.append(None if quote is None else (quote.bid, quote.ask, quote.midpoint))
        return ()


class RequestGuardStrategy(StrategyBase):
    strategy_id = "request-guard"

    def __init__(self) -> None:
        self.market_error = ""
        self.clock_quote = None
        self.clock_latest = None

    def on_market(self, context: StrategyContext, event):
        try:
            context.request_quote("BTC/USDT", venue="binance", market="spot")
        except RuntimeError as error:
            self.market_error = str(error)
        return ()

    def on_clock(self, context: StrategyContext, event):
        self.clock_quote = context.request_quote("BTC/USDT", venue="binance", market="spot")
        self.clock_latest = context.market.latest_quote("BTC/USDT", venue="binance", market="spot")
        return ()


class StaticQuoteProvider:
    def fetch_quote(self, instrument, *, params=None):
        return {
            "time": "2026-01-01T00:05:00+00:00",
            "bid": "102",
            "ask": "103",
            "bid_size": "1.5",
            "ask_size": "2.5",
        }


class MixedEventSource:
    def __init__(self, events):
        self._events = tuple(events)

    def events(self):
        return iter(self._events)


def test_strategy_runtime_runs_start_market_end_callbacks_from_data_view() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write("market.ohlcv.btc_usdt.1m", [
            {"time": "2026-01-01T00:00:00+00:00", "close": 100},
            {"time": "2026-01-01T00:01:00+00:00", "close": 101},
        ])
        data = DataContext(store)
        bars = data.attach("bars", dataset="market.ohlcv.btc_usdt.1m")
        strategy = RecordingStrategy()

        result = StrategyRuntime(strategy, data).run(DataViewEventSource(bars))

        assert result.strategy_id == "recording"
        assert result.event_count == 2
        assert [record.hook for record in result.callbacks] == [
            "on_start",
            "on_market",
            "on_market",
            "on_end",
        ]
        assert [intent["kind"] for intent in result.intents] == [
            "started",
            "seen",
            "seen",
            "ended",
        ]
        assert strategy.calls[1:] == [
            ("market", 100),
            ("market", 101),
            ("end", result.last_event.time),
        ]


def test_strategy_runtime_publishes_component_views_before_strategy_callback() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write("market.ohlcv.btc_usdt.1m", [
            {"time": "2026-01-01T00:00:00+00:00", "close": 100},
            {"time": "2026-01-01T00:01:00+00:00", "close": 101},
        ])
        data = DataContext(store)
        bars = data.attach("bars", dataset="market.ohlcv.btc_usdt.1m")
        strategy = ViewReadingStrategy()

        runtime = StrategyRuntime(strategy, data, components=(MarketCurrentProjection(),))
        result = runtime.run(DataViewEventSource(bars))

        market = runtime.views.require("market.current")
        assert result.event_count == 2
        assert strategy.seen == [(1, 100), (2, 101)]
        assert market.event_count == 2
        assert runtime.views.snapshot()["views"]["market.current"]["owner"] == "system"


def test_runtime_account_and_system_events_share_event_line_without_market_callback() -> None:
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))
    strategy = RecordingStrategy()
    account = AccountContext(AccountRef("simulated", "strategy-a"), Environment.SIMULATION)
    account_key = "account.current.simulation.simulated.strategy_a"
    source = MixedEventSource((
        AccountRuntimeEvent(
            account,
            1,
            datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
            payload={"equity": "1000", "source": "simulated"},
            snapshot=AccountSnapshot(
                account,
                balances=(
                    AccountBalance.from_total_locked(
                        "USD",
                        Decimal("1000"),
                        Decimal("0"),
                        source=AccountSource.SIMULATED,
                    ),
                ),
                observed_at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
                source=AccountSource.SIMULATED,
            ),
        ),
        next(IterableEventSource("bars", [
            {"time": "2026-01-01T00:01:00+00:00", "close": 101},
        ]).events()),
        AccountRuntimeEvent(
            account,
            2,
            datetime.fromisoformat("2026-01-01T00:02:00+00:00"),
            payload={"equity": "1015", "source": "simulated"},
            snapshot=AccountSnapshot(
                account,
                balances=(
                    AccountBalance.from_total_locked(
                        "USD",
                        Decimal("1015"),
                        Decimal("0"),
                        source=AccountSource.SIMULATED,
                    ),
                ),
                observed_at=datetime.fromisoformat("2026-01-01T00:02:00+00:00"),
                source=AccountSource.SIMULATED,
            ),
        ),
        SystemRuntimeEvent(
            "risk.limit.updated",
            1,
            datetime.fromisoformat("2026-01-01T00:03:00+00:00"),
            payload={"limit": "reduce-only"},
        ),
    ))

    runtime = StrategyRuntime(
        strategy,
        data,
        components=(AccountCurrentProjection(account), MarketCurrentProjection(), SystemEventProjection()),
    )
    result = runtime.run(source)

    account_view = runtime.views.require(account_key)
    system_events = runtime.views.require("system.events")
    system_strategy = runtime.views.require("system.strategy")
    assert result.event_count == 1
    assert result.runtime_event_count == 4
    assert [record.hook for record in result.callbacks] == ["on_start", "on_market", "on_end"]
    assert account_view.event_count == 2
    assert account_view.equity == Decimal("1015")
    assert account_view.initial_equity == Decimal("1000")
    assert account_view.net_profit == Decimal("15")
    assert account_view.total_return == Decimal("0.015")
    assert system_events.event_count == 1
    assert system_events.last_name == "risk.limit.updated"
    assert system_strategy.event_count == 1
    assert system_strategy.runtime_event_count == 4
    assert system_strategy.last_runtime_stream == "system"


def test_strategy_context_account_accessor_requires_key_when_multiple_accounts_exist() -> None:
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))
    account_a = AccountContext(AccountRef("simulated", "strategy-a"), Environment.BACKTEST)
    account_b = AccountContext(AccountRef("simulated", "strategy-b"), Environment.BACKTEST)
    projection_a = AccountCurrentProjection(account_a)
    projection_b = AccountCurrentProjection(account_b)
    runtime = StrategyRuntime(
        RecordingStrategy(),
        data,
        components=(projection_a, projection_b),
    )
    runtime.run(MixedEventSource(()))
    context = StrategyContext(data, views=runtime.views)

    assert context.account(projection_a.key).context == account_a
    with pytest.raises(ValueError, match="multiple account views"):
        context.account()


def test_context_subscribe_quote_and_market_latest_quote_are_runtime_owned() -> None:
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))
    strategy = QuoteReadingStrategy()
    source = IterableEventSource("binance.quote.BTC/USDT", [
        {
            "time": "2026-01-01T00:00:00+00:00",
            "kind": "quote",
            "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
            "bid": "100",
            "ask": "101",
        }
    ])

    runtime = StrategyRuntime(strategy, data)
    result = runtime.run(source)

    assert result.event_count == 1
    assert strategy.subscription.key == "market.quote.binance_spot_btc_usdt"
    assert len(runtime.subscriptions.list()) == 1
    assert str(strategy.seen[0][0]) == "100"
    assert str(strategy.seen[0][1]) == "101"
    assert str(strategy.seen[0][2]) == "100.5"
    market_view = runtime.views.require("market.quotes")
    assert market_view.event_count == 1
    assert market_view.quotes[0].market_id == "market:binance:spot:btc_usdt"
    assert market_view.quotes[0].instrument_id == "instrument:spot:btc:usdt"
    assert market_view.quotes[0].market_key == "binance_spot_btc_usdt"
    assert str(market_view.quotes[0].bid) == "100"
    assert str(market_view.quotes[0].ask) == "101"


def test_request_quote_is_only_allowed_during_on_clock_and_updates_market_view() -> None:
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))
    strategy = RequestGuardStrategy()
    source = MixedEventSource((
        next(IterableEventSource("binance.quote.BTC/USDT", [
            {
                "time": "2026-01-01T00:00:00+00:00",
                "kind": "quote",
                "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                "bid": "100",
                "ask": "101",
            }
        ]).events()),
        ClockEvent(time=datetime.fromisoformat("2026-01-01T00:05:00+00:00")),
    ))

    runtime = StrategyRuntime(strategy, data, quote_provider=StaticQuoteProvider())
    runtime.run(source)

    assert strategy.market_error == "market requests are only allowed during on_clock"
    assert strategy.clock_quote is strategy.clock_latest
    assert str(strategy.clock_quote.bid) == "102"
    assert str(strategy.clock_quote.ask) == "103"
    market_view = runtime.views.require("market.quotes")
    assert market_view.event_count == 2
    assert str(market_view.quotes[0].bid) == "102"


def test_strategy_runtime_still_calls_start_and_end_without_events() -> None:
    strategy = RecordingStrategy()
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))

    result = StrategyRuntime(strategy, data).run(IterableEventSource("empty", ()))

    assert result.event_count == 0
    assert result.last_event is None
    assert [record.hook for record in result.callbacks] == ["on_start", "on_end"]
    assert strategy.calls == [("start", None), ("end", None)]


def test_iterable_event_source_requires_timezone_aware_time() -> None:
    source = IterableEventSource("bad", [{"time": "2026-01-01T00:00:00", "close": 100}])

    try:
        list(source.events())
    except ValueError as error:
        assert "timezone-aware" in str(error)
    else:
        raise AssertionError("expected timezone-aware validation")


def test_strategy_runtime_example_runs(capsys) -> None:
    runpy.run_path("examples/strategy_runtime.py", run_name="__main__")

    captured = capsys.readouterr()
    assert '"strategy_id": "momentum-printer"' in captured.out
    assert '"signal": "up"' in captured.out
    assert '"signal": "down"' in captured.out


def test_hyperliquid_clock_quote_example_runs_with_fixture(capsys, monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["examples/hyperliquid_clock_quote.py"])

    runpy.run_path("examples/hyperliquid_clock_quote.py", run_name="__main__")

    output = json.loads(capsys.readouterr().out)
    assert output["strategy_id"] == "hyperliquid-clock-quote"
    assert output["callbacks"] == ["on_start", "on_market", "on_clock", "on_end"]
    assert output["intents"][0]["type"] == "subscribed"
    assert output["intents"][1]["type"] == "stream_quote"
    assert output["intents"][2]["type"] == "clock_quote"
    assert output["intents"][2]["latest_matches_request"] is True


def test_strategy_views_example_runs(capsys) -> None:
    runpy.run_path("examples/strategy_views.py", run_name="__main__")

    output = json.loads(capsys.readouterr().out)
    assert output["strategy_id"] == "view-demo"
    assert output["event_count"] == 2
    assert output["project_regime"]["name"] == "risk-on"
    assert output["strategy_counter"]["event_count"] == 2
    assert output["market_current"]["event_count"] == 2
    assert output["system_control"]["total_count"] == 1
    assert output["system_intents"]["total_count"] == 1
    assert output["system_strategy"]["status"] == "finished"
    assert output["view_keys"] == [
        "market.current",
        "market.quotes",
        "project.regime",
        "strategy.counter",
        "system.control",
        "system.data",
        "system.intents",
        "system.strategy",
    ]


def test_runtime_modes_backtest_paper_example_runs_on_same_line(capsys) -> None:
    runpy.run_path("examples/runtime_modes_backtest_paper.py", run_name="__main__")

    output = json.loads(capsys.readouterr().out)
    assert output["backtest"]["mode"] == "backtest"
    assert output["paper"]["mode"] == "paper"
    assert output["backtest"]["event_count"] == 2
    assert output["paper"]["event_count"] == 2
    assert output["backtest"]["final_equity"] == "1010"
    assert output["paper"]["final_equity"] == "1010"
    assert output["backtest"]["account_view"]["net_profit"] == "10"
    assert output["paper"]["account_view"]["net_profit"] == "10"


def test_hyperliquid_stream_strategy_example_consumes_async_rows(capsys) -> None:
    module = runpy.run_path("examples/hyperliquid_stream_strategy.py")

    async def rows():
        yield {
            "time": "2026-01-01T00:00:00+00:00",
            "kind": "orderbook",
            "bid1": "100",
            "ask1": "101",
        }
        yield {
            "time": "2026-01-01T00:00:01+00:00",
            "kind": "orderbook",
            "bid1": "100.5",
            "ask1": "101.5",
        }

    async def run() -> int:
        data = DataContext(DataStore(":unused:", storage_format="jsonl"))
        return await module["run_stream_strategy"](
            rows(),
            stream="hyperliquid.orderbook.BTC/USDC:USDC",
            data=data,
            strategy=module["StreamMarketPrinter"](),
            limit=2,
        )

    assert asyncio.run(run()) == 2
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["type"] for line in lines] == [
        "strategy_started",
        "market_data_seen",
        "market_data_seen",
        "strategy_finished",
    ]
    assert lines[1]["sequence"] == 1
    assert lines[1]["payload"]["bid1"] == "100"
    assert lines[-1]["last_time"] == "2026-01-01T00:00:01+00:00"
