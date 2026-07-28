from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from tempfile import TemporaryDirectory

import pytest

from kairospy.core.account import (
    AccountBalance,
    AccountContext,
    AccountRef,
    AccountSnapshot,
    AccountSource,
    Environment,
)
from kairospy.application.context import DataContext, StrategyContext
from kairospy.infrastructure.data import DataStore
from kairospy.core.execution import ExecutionCoordinator, cash_order_request
from kairospy.core.market import (
    Bar,
    MarketEvent,
    MarketSubject,
    OrderBookSnapshot,
    Quote,
    RateObservation,
    TradePrint,
)
from kairospy.core.order import OrderSide
from kairospy.core.order import OrderEvent, OrderEventKind
from kairospy.application.runtime.kernel import RuntimeDataPipeline, RuntimeKernel, RuntimeRequestProviders
from kairospy.application.runtime.model import (
    BACKTEST_PROFILE,
    ExecutionRuntimePayload,
    RuntimeDataEnvelope,
    account_data_envelope,
    system_data_envelope,
)
from kairospy.application.runtime.projection import MarketState, SystemEventProjection
from kairospy.application.runtime.projection.account import AccountCurrentProjection
from kairospy.application.runtime.projection.execution import ExecutionCurrentProjection
from kairospy.application.runtime.projection.market import MarketCurrentProjection
from kairospy.application.runtime.run import RuntimeProjectionConfig, RuntimeRunner, RuntimeRunSpec, RuntimeServiceConfig, RuntimeStateConfig
from kairospy.application.service.domains.market import (
    DataViewEventSource,
    IterableEventSource,
    STREAM_BAR,
    STREAM_MARKET_CONTEXT,
    STREAM_ORDERBOOK,
    STREAM_RATE,
    STREAM_TICKER,
    STREAM_TRADE,
)
from kairospy.application.strategy import StrategyBase


BAR_CLOSE_SUMMARY_FIELD = "Bar.close"
BAR_OPEN_SUMMARY_FIELD = "Bar.open"
ORDERBOOK_BID1_SUMMARY_FIELD = "OrderBookSnapshot.bid1"
TRADE_PRICE_SUMMARY_FIELD = "TradePrint.price"


class RecordingStrategy(StrategyBase):
    strategy_id = "recording"

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def on_start(self, context: StrategyContext):
        self.calls.append(("start", context.now))
        return ({"kind": "started"},)

    def on_market(self, context: StrategyContext, event):
        close = _latest_market_field(context, BAR_CLOSE_SUMMARY_FIELD)
        self.calls.append(("market", close))
        assert context.now == event.time
        assert context.stream == "bars"
        return ({"kind": "seen", "close": close},)

    def on_end(self, context: StrategyContext):
        self.calls.append(("end", context.now))
        return ({"kind": "ended"},)


class RuntimeMessageStrategy(StrategyBase):
    strategy_id = "runtime-messages"

    def __init__(self) -> None:
        self.messages: list[tuple[str, str | None, str, object]] = []

    def on_account(self, context: StrategyContext, event):
        account_event = context.latest_data(domain="account")
        self.messages.append(("account", context.stream, context.phase, account_event.payload.context.account.account_id))
        return ({"kind": "account_seen", "sequence": event.sequence},)

    def on_order(self, context: StrategyContext, event):
        order_event = context.latest_data(domain="execution")
        self.messages.append(("order", context.stream, context.phase, order_event.payload.order_event.client_order_id))
        return ({"kind": "order_seen", "sequence": event.sequence},)

    def on_system(self, context: StrategyContext, event):
        self.messages.append(("system", context.stream, context.phase, context.system_event.kind))
        return ({"kind": "system_seen", "sequence": event.sequence},)


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
        self.subscription = context.subscribe_market_data(
            "BTC/USDT",
            selectors=(Quote.select("bid", "ask", basis="ticker"),),
            venue="binance",
            market="spot",
        )
        return ()

    def on_market(self, context: StrategyContext, event):
        quote = context.market.latest_quote("BTC/USDT", venue="binance", market="spot")
        self.seen.append(None if quote is None else (quote.bid, quote.ask, quote.midpoint))
        return ()


class MarketSubscriptionStrategy(StrategyBase):
    strategy_id = "market-subscriptions"

    def __init__(self) -> None:
        self.subscriptions = []
        self.latest_rate = None
        self.latest_funding = None

    def on_start(self, context: StrategyContext):
        self.subscriptions.append(context.subscribe_market_data(
            "BTC/USDT",
            selectors=(Quote.select("bid", "ask", basis="ticker"),),
            venue="binance",
            market="spot",
        ))
        self.subscriptions.append(context.subscribe_market_data(
            "BTC/USDT",
            selectors=(OrderBookSnapshot.select("bid1", "ask1", depth=10),),
            venue="binance",
            market="spot",
        ))
        self.subscriptions.append(context.subscribe_market_data(
            "BTC/USDT",
            selectors=(Bar.select("open", "close", interval="1m"),),
            venue="binance",
            market="spot",
        ))
        self.subscriptions.append(context.subscribe_market_data(
            "BTC/USDT",
            selectors=(TradePrint.select("price", "size"),),
            venue="binance",
            market="spot",
        ))
        self.subscriptions.append(context.subscribe_subject_data("rate", "USD.SOFR", selectors=(RateObservation.select("rate"),)))
        self.subscriptions.append(context.subscribe_market_data(
            "BTC/USDT",
            selectors=(RateObservation.select("rate", basis="funding_rate"),),
            venue="binance",
            market="perp",
        ))
        return ()

    def on_market(self, context: StrategyContext, event):
        self.latest_rate = context.market.latest_rate("USD.SOFR")
        self.latest_funding = context.market.latest_funding("BTC/USDT", venue="binance", market="perp")
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


class StaticMarketDataProvider:
    def fetch_quote(self, instrument, *, params=None):
        return {
            "time": "2026-01-01T00:05:00+00:00",
            "bid": "102",
            "ask": "103",
            "bid_size": "1.5",
            "ask_size": "2.5",
        }


class DataflowReadingStrategy(StrategyBase):
    strategy_id = "dataflow-reader"

    def __init__(self) -> None:
        self.latest = None
        self.counts: list[int] = []

    def on_market(self, context: StrategyContext, event):
        self.latest = context.latest_data(domain="market")
        self.counts.append(len(context.data_records(domain="market")))
        return ()


class RuntimeLifecycleStrategy(StrategyBase):
    strategy_id = "runtime-lifecycle"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def on_start(self, context: StrategyContext):
        self.calls.append("on_start")

    def on_market(self, context: StrategyContext, event):
        self.calls.append("on_market")
        context.target_position("instrument:spot:btc:usdt", Decimal("1"), intent_id="target-btc")

    def on_system(self, context: StrategyContext, event):
        self.calls.append("on_system")

    def on_account(self, context: StrategyContext, event):
        self.calls.append("on_account")

    def on_end(self, context: StrategyContext):
        self.calls.append("on_end")


class MixedEventSource:
    def __init__(self, events):
        self._events = tuple(events)

    def events(self):
        return iter(self._events)


def _latest_market_field(context: StrategyContext, field: str) -> object:
    view = context.views.require("market.fields")
    for item in reversed(tuple(view.fields)):
        if item.field == field:
            return item.value
    raise AssertionError(f"missing market field: {field}")


def test_runtime_lifecycle_processes_intent_follow_ups_through_same_event_loop() -> None:
    observed_at = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    account = AccountContext(AccountRef("simulated", "strategy-a"), Environment.SIMULATION)
    strategy = RuntimeLifecycleStrategy()
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))
    market_event = RuntimeDataEnvelope(
        "market",
        "bar",
        observed_at,
        1,
        MarketEvent(
            MarketSubject("instrument", "instrument:spot:btc:usdt"),
            observed_at,
            Bar(
                instrument_id="instrument:spot:btc:usdt",
                time=observed_at,
                market_key="test_spot_btc_usdt",
                timeframe="1m",
                close=Decimal("101"),
                source="test",
            ),
            source="test",
        ),
        stream="test.bar",
        source="test",
    )

    def handle_intents(intents, context: StrategyContext, hook: str):
        if hook != "on_market" or not intents:
            return ()
        return (
            account_data_envelope(
                account,
                sequence=2,
                time=context.now,
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
                    observed_at=context.now,
                    source=AccountSource.SIMULATED,
                ),
                equity=Decimal("1000"),
                source=AccountSource.SIMULATED,
            ),
        )

    result = RuntimeRunner.run(
        RuntimeRunSpec(
            run_id="run-lifecycle",
            profile=BACKTEST_PROFILE,
            strategy=strategy,
            source=MixedEventSource((market_event,)),
            state_config=RuntimeStateConfig(data),
            service_config=RuntimeServiceConfig(intent_handler=handle_intents),
            started_at=observed_at,
        )
    )

    assert strategy.calls == ["on_start", "on_system", "on_market", "on_account", "on_end"]
    assert result.runtime.event_count == 1
    assert result.runtime.runtime_event_count == 3
    assert [record.hook for record in result.runtime.callbacks] == [
        "on_start",
        "on_system",
        "on_market",
        "on_account",
        "on_end",
    ]


def test_strategy_runtime_runs_start_market_end_callbacks_from_data_view() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write("market.ohlcv.btc_usdt.1m", [
            {
                "time": "2026-01-01T00:00:00+00:00",
                "market_id": "market:binance:spot:btc_usdt",
                "instrument_id": "instrument:spot:btc:usdt",
                "market_key": "binance_spot_btc_usdt",
                "close": 100,
            },
            {
                "time": "2026-01-01T00:01:00+00:00",
                "market_id": "market:binance:spot:btc_usdt",
                "instrument_id": "instrument:spot:btc:usdt",
                "market_key": "binance_spot_btc_usdt",
                "close": 101,
            },
        ])
        data = DataContext(store)
        bars = data.attach("bars", dataset="market.ohlcv.btc_usdt.1m")
        strategy = RecordingStrategy()

        result = RuntimeKernel(strategy, data).run(DataViewEventSource(bars))

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


def test_runtime_runner_executes_runtime_run_spec() -> None:
    observed_at = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    strategy = RecordingStrategy()
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))
    account = AccountContext(AccountRef("simulated", "strategy-a"), Environment.BACKTEST)
    account_projection = AccountCurrentProjection(
        account,
        initial_equity=Decimal("1000"),
    )
    spec = RuntimeRunSpec(
        run_id="run-a",
        profile=BACKTEST_PROFILE,
        strategy=strategy,
        source=IterableEventSource(
            "bars",
            (
                {
                    "time": observed_at.isoformat(),
                    "kind": "bar",
                    "instrument_id": "instrument:spot:btc:usdt",
                    "close": "100",
                },
            ),
        ),
        state_config=RuntimeStateConfig(data),
        projection_config=RuntimeProjectionConfig((account_projection,)),
        pre_events=(
            account_data_envelope(
                account,
                sequence=1,
                time=observed_at,
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
                    observed_at=observed_at,
                    source=AccountSource.SIMULATED,
                ),
                equity=Decimal("1000"),
                source=AccountSource.SIMULATED,
            ),
        ),
        started_at=observed_at,
    )

    result = RuntimeRunner.run(spec)

    assert result.runtime.strategy_id == "recording"
    assert result.runtime.event_count == 1
    assert result.views.require(account_projection.key).equity == Decimal("1000")


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

        runtime = RuntimeKernel(strategy, data, components=(MarketCurrentProjection(),))
        result = runtime.run(DataViewEventSource(bars))

        market = runtime.views.require("market.current")
        assert result.event_count == 2
        assert strategy.seen == [(1, 100), (2, 101)]
        assert market.event_count == 2
        assert runtime.views.snapshot()["views"]["market.current"]["owner"] == "system"


def test_runtime_account_and_system_events_share_event_line_with_domain_callbacks() -> None:
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))
    strategy = RecordingStrategy()
    account = AccountContext(AccountRef("simulated", "strategy-a"), Environment.SIMULATION)
    account_key = "account.current.simulation.simulated.strategy_a"
    source = MixedEventSource((
        account_data_envelope(
            account,
            sequence=1,
            time=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
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
            equity=Decimal("1000"),
            source=AccountSource.SIMULATED,
            stream="account",
        ),
        next(IterableEventSource("bars", [
            {
                "time": "2026-01-01T00:01:00+00:00",
                "market_id": "market:binance:spot:btc_usdt",
                "instrument_id": "instrument:spot:btc:usdt",
                "market_key": "binance_spot_btc_usdt",
                "close": 101,
            },
        ]).events()),
        account_data_envelope(
            account,
            sequence=2,
            time=datetime.fromisoformat("2026-01-01T00:02:00+00:00"),
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
            equity=Decimal("1015"),
            source=AccountSource.SIMULATED,
            stream="account",
        ),
        system_data_envelope(
            "risk.limit.updated",
            sequence=1,
            time=datetime.fromisoformat("2026-01-01T00:03:00+00:00"),
            payload={"limit": "reduce-only"},
        ),
    ))

    runtime = RuntimeKernel(
        strategy,
        data,
        components=(AccountCurrentProjection(account), MarketCurrentProjection(), SystemEventProjection()),
    )
    result = runtime.run(source)

    account_view = runtime.views.require(account_key)
    risk_events = runtime.views.require("risk.events")
    system_events = runtime.views.require("system.events")
    system_strategy = runtime.views.require("system.strategy")
    assert result.event_count == 1
    assert result.runtime_event_count == 4
    assert [record.hook for record in result.callbacks] == ["on_start", "on_account", "on_market", "on_account", "on_system", "on_end"]
    assert account_view.event_count == 2
    assert account_view.equity == Decimal("1015")
    assert account_view.initial_equity == Decimal("1000")
    assert account_view.net_profit == Decimal("15")
    assert account_view.total_return == Decimal("0.015")
    assert system_events.event_count == 1
    assert system_events.last_name == "risk.limit.updated"
    assert risk_events.event_count == 1
    assert risk_events.last_name == "risk.limit.updated"
    assert risk_events.last_payload == {"limit": "reduce-only"}
    assert system_strategy.event_count == 1
    assert system_strategy.runtime_event_count == 4
    assert system_strategy.last_runtime_stream == "system"


def test_strategy_runtime_dispatches_account_order_and_system_messages_to_strategy_hooks() -> None:
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))
    account = AccountContext(AccountRef("simulated", "strategy-a"), Environment.SIMULATION)
    strategy = RuntimeMessageStrategy()
    account_time = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    order_time = datetime.fromisoformat("2026-01-01T00:01:00+00:00")
    system_time = datetime.fromisoformat("2026-01-01T00:02:00+00:00")
    source = MixedEventSource((
        account_data_envelope(
            account,
            sequence=1,
            time=account_time,
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
                observed_at=account_time,
                source=AccountSource.SIMULATED,
            ),
            equity=Decimal("1000"),
            source=AccountSource.SIMULATED,
            stream="account",
        ),
        RuntimeDataEnvelope(
            "execution",
            "order",
            order_time,
            1,
            ExecutionRuntimePayload(order_event=OrderEvent("client-1", OrderEventKind.ACKNOWLEDGED, order_time, venue_order_id="venue-1")),
            stream="order",
            metadata={"adapter": "unit-test"},
        ),
        system_data_envelope("risk.limit.updated", sequence=1, time=system_time, payload={"limit": "reduce-only"}),
    ))

    result = RuntimeKernel(strategy, data).run(source)

    assert result.event_count == 0
    assert result.runtime_event_count == 3
    assert [record.hook for record in result.callbacks] == [
        "on_start",
        "on_account",
        "on_order",
        "on_system",
        "on_end",
    ]
    assert strategy.messages == [
        ("account", "account", "account", "strategy-a"),
        ("order", "order", "order", "client-1"),
        ("system", "system", "system", "risk.limit.updated"),
    ]
    assert [item["kind"] for item in result.intents] == ["account_seen", "order_seen", "system_seen"]


def test_strategy_context_account_accessor_requires_key_when_multiple_accounts_exist() -> None:
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))
    account_a = AccountContext(AccountRef("simulated", "strategy-a"), Environment.BACKTEST)
    account_b = AccountContext(AccountRef("simulated", "strategy-b"), Environment.BACKTEST)
    projection_a = AccountCurrentProjection(account_a)
    projection_b = AccountCurrentProjection(account_b)
    runtime = RuntimeKernel(
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

    runtime = RuntimeKernel(strategy, data)
    result = runtime.run(source)

    assert result.event_count == 1
    assert strategy.subscription.key.startswith("market.data.btc_usdt.")
    assert tuple(selector.key for selector in strategy.subscription.spec.selectors) == ("Quote|bid.ask|basis=ticker",)
    assert [plan.channel for plan in strategy.subscription.stream_plans] == [STREAM_TICKER]
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
    subscriptions = runtime.views.require("market.subscriptions")
    assert subscriptions.total_count == 1
    assert subscriptions.active_count == 1
    assert subscriptions.subscriptions[0].subject_type == "instrument"
    assert subscriptions.subscriptions[0].kind == "quote"
    assert subscriptions.subscriptions[0].fields == ("Quote|bid.ask|basis=ticker",)
    assert subscriptions.subscriptions[0].last_event_time == datetime.fromisoformat("2026-01-01T00:00:00+00:00")


def test_market_subscriptions_cover_rates_and_funding_views() -> None:
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))
    strategy = MarketSubscriptionStrategy()
    source = MixedEventSource((
        next(IterableEventSource("rates.usd.sofr", [
            {
                "time": "2026-01-01T00:00:00+00:00",
                "kind": "interest_rate",
                "subject_type": "rate",
                "subject_id": "USD.SOFR",
                "rate_id": "USD.SOFR",
                "rate": "0.0525",
                "basis": "ACT/360",
            }
        ]).events()),
        next(IterableEventSource("binance.funding.BTC/USDT", [
            {
                "time": "2026-01-01T00:01:00+00:00",
                "kind": "funding_rate",
                "market_id": "market:binance:perp:btc_usdt",
                "instrument_id": "instrument:perp:btc:usdt",
                "market_key": "binance_perp_btc_usdt",
                "rate": "0.0001",
            }
        ]).events()),
    ))

    runtime = RuntimeKernel(strategy, data)
    runtime.run(source)

    assert [item.kind for item in strategy.subscriptions] == ["quote", "orderbook", "bar", "trade", "rate", "rate"]
    assert [item.stream_plans[0].channel for item in strategy.subscriptions] == [
        STREAM_TICKER,
        STREAM_ORDERBOOK,
        STREAM_BAR,
        STREAM_TRADE,
        STREAM_RATE,
        STREAM_MARKET_CONTEXT,
    ]
    assert strategy.latest_rate.rate == Decimal("0.0525")
    assert strategy.latest_funding.rate == Decimal("0.0001")
    rates = runtime.views.require("market.rates")
    subscriptions = runtime.views.require("market.subscriptions")
    assert rates.event_count == 2
    assert {item.rate_id for item in rates.rates} == {"USD.SOFR", "market:binance:perp:btc_usdt"}
    assert subscriptions.total_count == 6
    assert sum(1 for item in subscriptions.subscriptions if item.last_event_time is not None) == 2


def test_market_state_projects_books_bars_trades_and_best_quote() -> None:
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))
    market_fields = {
        "market_id": "market:binance:spot:btc_usdt",
        "instrument_id": "instrument:spot:btc:usdt",
        "market_key": "binance_spot_btc_usdt",
    }
    source = MixedEventSource((
        next(IterableEventSource("binance.book.BTC/USDT", [
            {
                "time": "2026-01-01T00:00:00+00:00",
                "kind": "orderbook",
                **market_fields,
                "bids": [["100", "1.2"]],
                "asks": [["101", "0.8"]],
            }
        ]).events()),
        next(IterableEventSource("binance.bar.BTC/USDT", [
            {
                "time": "2026-01-01T00:01:00+00:00",
                "kind": "bar",
                **market_fields,
                "timeframe": "1m",
                "open": "100",
                "high": "102",
                "low": "99",
                "close": "101",
                "volume": "10",
            }
        ]).events()),
        next(IterableEventSource("binance.trade.BTC/USDT", [
            {
                "time": "2026-01-01T00:01:30+00:00",
                "kind": "trade",
                **market_fields,
                "id": "trade-1",
                "side": "buy",
                "price": "101",
                "size": "0.5",
                "cost": "50.5",
            }
        ]).events()),
    ))

    runtime = RuntimeKernel(StrategyBase(), data)
    runtime.run(source)

    quotes = runtime.views.require("market.quotes")
    books = runtime.views.require("market.books")
    bars = runtime.views.require("market.bars")
    trades = runtime.views.require("market.trades")
    fields = runtime.views.require("market.fields")
    assert quotes.event_count == 1
    assert str(quotes.quotes[0].bid) == "100"
    assert books.event_count == 1
    assert books.books[0].bid_depth == 1
    assert str(books.books[0].asks[0][0]) == "101"
    assert bars.event_count == 1
    assert bars.bars[0].timeframe == "1m"
    assert str(bars.bars[0].close) == "101"
    assert trades.event_count == 1
    assert trades.trades[0].trade_id == "trade-1"
    assert str(trades.trades[0].size) == "0.5"
    field_values = {(item.field, item.interval): item.value for item in fields.fields}
    assert str(field_values[(ORDERBOOK_BID1_SUMMARY_FIELD, None)]) == "100"
    assert str(field_values[(BAR_OPEN_SUMMARY_FIELD, "1m")]) == "100"
    assert str(field_values[(TRADE_PRICE_SUMMARY_FIELD, None)]) == "101"


def test_market_field_subscription_can_target_hourly_open_without_subscribing_to_bar_type() -> None:
    class HourlyOpenStrategy(StrategyBase):
        def __init__(self) -> None:
            self.subscription = None

        def on_start(self, context: StrategyContext):
            self.subscription = context.subscribe_market_data(
                "BTC/USDT",
                selectors=(Bar.select("open", interval="1h"),),
                venue="binance",
                market="spot",
            )
            return ()

    data = DataContext(DataStore(":unused:", storage_format="jsonl"))
    strategy = HourlyOpenStrategy()
    source = IterableEventSource("binance.bar.BTC/USDT", [
        {
            "time": "2026-01-01T01:00:00+00:00",
            "kind": "bar",
            "market_id": "market:binance:spot:btc_usdt",
            "instrument_id": "instrument:spot:btc:usdt",
            "market_key": "binance_spot_btc_usdt",
            "timeframe": "1h",
            "open": "100",
            "close": "101",
        }
    ])

    runtime = RuntimeKernel(strategy, data)
    runtime.run(source)

    assert strategy.subscription.spec.selectors[0].key == "Bar|open|interval=1h"
    assert strategy.subscription.stream_plans[0].channel == STREAM_BAR
    fields = runtime.views.require("market.fields")
    assert fields.event_count == 2
    assert [(item.field, item.interval, str(item.value)) for item in fields.fields] == [
        (BAR_CLOSE_SUMMARY_FIELD, "1h", "101"),
        (BAR_OPEN_SUMMARY_FIELD, "1h", "100"),
    ]


def test_market_domain_applies_integration_market_event_into_views() -> None:
    state = MarketState()
    observed_at = datetime.fromisoformat("2026-01-01T01:00:00+00:00")
    event = MarketEvent(
        MarketSubject("instrument", "instrument:spot:btc:usdt"),
        observed_at,
        Bar(
            instrument_id="instrument:spot:btc:usdt",
            market_id="market:binance:spot:btc_usdt",
            market_key="binance_spot_btc_usdt",
            time=observed_at,
            timeframe="1h",
            open=Decimal("100"),
            close=Decimal("101"),
            source="binance",
        ),
        source="binance",
    )

    summaries = state.apply_market_event(event)

    assert [(item.field, item.interval, str(item.value)) for item in summaries] == [
        (BAR_OPEN_SUMMARY_FIELD, "1h", "100"),
        (BAR_CLOSE_SUMMARY_FIELD, "1h", "101"),
    ]
    fields = state.fields_view()
    observations = state.observations_view()
    assert fields.event_count == 2
    assert observations.event_count == 1
    assert observations.observations[0].kind == "bar"
    assert [(item.field, item.interval, str(item.value)) for item in fields.fields] == [
        (BAR_CLOSE_SUMMARY_FIELD, "1h", "101"),
        (BAR_OPEN_SUMMARY_FIELD, "1h", "100"),
    ]


def test_market_observations_view_covers_generic_curve_and_index_subjects() -> None:
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))
    source = MixedEventSource((
        next(IterableEventSource("curves.usd.ois", [
            {
                "time": "2026-01-01T00:00:00+00:00",
                "kind": "curve_point",
                "subject_type": "curve",
                "subject_id": "USD.OIS",
                "tenor": "1M",
                "rate": "0.05",
            }
        ]).events()),
        next(IterableEventSource("index.spx", [
            {
                "time": "2026-01-01T00:01:00+00:00",
                "kind": "index_value",
                "subject_type": "index",
                "subject_id": "SPX",
                "value": "5000",
            }
        ]).events()),
    ))

    runtime = RuntimeKernel(StrategyBase(), data)
    runtime.run(source)

    observations = runtime.views.require("market.observations")
    assert observations.event_count == 2
    assert {(item.subject_type, item.subject_id, item.kind) for item in observations.observations} == {
        ("curve", "USD.OIS", "curve_point"),
        ("index", "SPX", "index_value"),
    }


def test_execution_current_projection_publishes_coordinator_state() -> None:
    account = AccountContext(AccountRef("simulated", "strategy-a"), Environment.SIMULATION)
    coordinator = ExecutionCoordinator()
    at = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    request = cash_order_request(
        client_order_id="order-1",
        context=account,
        instrument_id="instrument:spot:btc:usdt",
        side=OrderSide.BUY,
        quantity=Decimal("2"),
    )
    coordinator.plan_order(
        request,
        reserve_currency="USD",
        reserve_amount=Decimal("100"),
        venue_snapshot=AccountSnapshot(
            account,
            balances=(AccountBalance.from_total_locked("USD", Decimal("1000"), Decimal("0"), source=AccountSource.SIMULATED),),
            observed_at=at,
            source=AccountSource.SIMULATED,
        ),
        at=at,
    )

    runtime = RuntimeKernel(
        RecordingStrategy(),
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        components=(ExecutionCurrentProjection(coordinator),),
    )
    runtime.run(IterableEventSource("empty", ()))

    view = runtime.views.require("execution.current")
    assert view.total_orders == 1
    assert view.active_orders == 1
    assert view.pending_reservations == 1
    assert view.latest_order.client_order_id == "order-1"
    assert view.orders[0].remaining_quantity == Decimal("2")


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
        RuntimeDataEnvelope(
            "clock",
            "clock",
            datetime.fromisoformat("2026-01-01T00:05:00+00:00"),
            1,
            {},
            stream="clock",
        ),
    ))

    runtime = RuntimeKernel(
        strategy,
        data,
        request_providers=RuntimeRequestProviders(market_data=StaticMarketDataProvider()),
    )
    runtime.run(source)

    assert strategy.market_error == "market requests are only allowed during on_clock"
    assert strategy.clock_quote is not strategy.clock_latest
    assert str(strategy.clock_latest.bid) == "100"
    assert str(strategy.clock_quote.bid) == "102"
    assert str(strategy.clock_quote.ask) == "103"
    market_view = runtime.views.require("market.quotes")
    assert market_view.event_count == 2
    assert str(market_view.quotes[0].bid) == "102"


def test_strategy_runtime_still_calls_start_and_end_without_events() -> None:
    strategy = RecordingStrategy()
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))

    result = RuntimeKernel(strategy, data).run(IterableEventSource("empty", ()))

    assert result.event_count == 0
    assert result.last_event is None
    assert [record.hook for record in result.callbacks] == ["on_start", "on_end"]
    assert strategy.calls == [("start", None), ("end", None)]


def test_runtime_data_pipeline_normalizes_runtime_events() -> None:
    event = next(IterableEventSource("binance.quote.BTC/USDT", [
        {
            "time": "2026-01-01T00:00:00+00:00",
            "kind": "quote",
            "market_id": "market:binance:spot:btc_usdt",
            "instrument_id": "instrument:spot:btc:usdt",
            "market_key": "binance_spot_btc_usdt",
            "bid": "100",
            "ask": "101",
        }
    ]).events())
    pipeline = RuntimeDataPipeline()

    envelope = pipeline.ingest(event)

    assert envelope.domain == "market"
    assert envelope.kind == "quote"
    assert envelope.stream == "binance.quote.BTC/USDT"
    assert pipeline.view().domain_counts == (("market", 1),)
    assert pipeline.latest(domain="market") is envelope


def test_strategy_runtime_applies_typed_market_data_envelope_without_legacy_event() -> None:
    observed_at = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    update = MarketEvent(
        MarketSubject("instrument", "instrument:spot:btc:usdt"),
        observed_at,
        Bar(
            instrument_id="instrument:spot:btc:usdt",
            time=observed_at,
            market_id="market:binance:spot:btc_usdt",
            market_key="binance_spot_btc_usdt",
            timeframe="1m",
            close=Decimal("101"),
            source="binance",
        ),
        source="binance",
    )
    source = MixedEventSource((
        RuntimeDataEnvelope("market", "bar", observed_at, 1, update, stream="binance.bar.BTC/USDT", source="binance"),
    ))

    runtime = RuntimeKernel(StrategyBase(), DataContext(DataStore(":unused:", storage_format="jsonl")))
    result = runtime.run(source)

    fields = runtime.views.require("market.fields")
    dataflow = runtime.views.require("system.dataflow")
    assert result.event_count == 1
    assert fields.event_count == 1
    assert fields.fields[0].field == BAR_CLOSE_SUMMARY_FIELD
    assert dataflow.total_count == 1
    assert dataflow.latest.payload_type == "MarketEvent"


def test_strategy_context_reads_runtime_data_pipeline() -> None:
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))
    strategy = DataflowReadingStrategy()
    source = IterableEventSource("binance.quote.BTC/USDT", [
        {
            "time": "2026-01-01T00:00:00+00:00",
            "kind": "quote",
            "market_id": "market:binance:spot:btc_usdt",
            "instrument_id": "instrument:spot:btc:usdt",
            "market_key": "binance_spot_btc_usdt",
            "bid": "100",
            "ask": "101",
        }
    ])

    runtime = RuntimeKernel(strategy, data)
    runtime.run(source)

    assert strategy.latest.domain == "market"
    assert strategy.latest.kind == "quote"
    assert strategy.counts == [1]
    view = runtime.views.require("system.dataflow")
    assert view.total_count == 1
    assert view.latest.domain == "market"
    assert view.latest.payload_type == "MarketEvent"


def test_iterable_event_source_requires_timezone_aware_time() -> None:
    source = IterableEventSource("bad", [{"time": "2026-01-01T00:00:00", "close": 100}])

    try:
        list(source.events())
    except ValueError as error:
        assert "timezone-aware" in str(error)
    else:
        raise AssertionError("expected timezone-aware validation")
