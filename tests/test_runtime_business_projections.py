from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.runtime.orchestration.pipeline import RuntimePortPipeline
from kairospy.application.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.runtime.dispatch.context import RuntimeContext
from kairospy.application.runtime.processors.system import runtime_processors
from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.application.runtime.ports import DataSubscription, MarketDataSubscriptionSpec
from kairospy.application.service.modes.backtest import BacktestExecutionService
from kairospy.core.account import AccountBalance, AccountContext, AccountRef, AccountSnapshot, AccountSource, AccountState, Environment
from kairospy.core.execution import ExecutionCoordinator, cash_order_request
from kairospy.core.intent import IntentJournal
from kairospy.core.market import Bar, OptionGreeks, OrderBookSnapshot, PriceLevel, Quote, RateObservation, TradePrint
from kairospy.core.order import OrderSide
from kairospy.core.reference import MarketRef, MarketResolver, ReferenceCatalog
from kairospy.core.views import ViewSchema, ViewStore


class EmptyStrategy:
    strategy_id = "s"

    def on_start(self, context: object) -> None:
        return None

    def on_data(self, context: object, signal: object) -> None:
        return None

    def on_intent(self, context: object, intent: object) -> None:
        return None

    def on_clock(self, context: object, signal: object) -> None:
        return None

    def on_system(self, context: object, signal: object) -> None:
        return None

    def on_end(self, context: object) -> None:
        return None


class FakeMarketDataService:
    key = "fake.market"
    schema = ViewSchema(key, "system", mutability="runtime_writable")

    def __init__(self) -> None:
        self.market = MarketRef.ephemeral(venue="binance", market="spot", source_symbol="BTC/USDT")
        self.subscription = DataSubscription(
            "sub.btc",
            MarketDataSubscriptionSpec(self.market, (Quote, Bar), identity="strategy-a"),
        )

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        if False:
            yield

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        return DataSubscription(spec.key, spec)

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        return None

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return (self.subscription,)

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def view(self) -> dict[str, object]:
        return {}


class FakeAccountService:
    key = "fake.account"
    schema = ViewSchema(key, "system", mutability="runtime_writable")

    def __init__(self) -> None:
        self.context = AccountContext(AccountRef("paper", "main"), Environment.PAPER)
        self._snapshot = AccountSnapshot(
            self.context,
            balances=(AccountBalance.from_total_locked("USDT", Decimal("1000"), Decimal("0"), source=AccountSource.SIMULATED),),
            observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            source=AccountSource.SIMULATED,
        )
        self._state = AccountState(
            self.context,
            self._snapshot.balances,
            (),
            (),
            (),
            self._snapshot.observed_at,
            AccountSource.SIMULATED,
        )

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        if False:
            yield

    def accounts(self) -> tuple[AccountContext, ...]:
        return (self.context,)

    def snapshot(self, account: AccountRef | None = None) -> AccountSnapshot | None:
        return self._snapshot

    def state(self, account: AccountRef | None = None) -> AccountState | None:
        return self._state

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def view(self) -> dict[str, object]:
        return {}


def test_market_view_state_publishes_business_views() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    views = ViewStore()
    data = FakeMarketDataService()
    pipeline = RuntimePortPipeline(
        views=views,
        processors=runtime_processors(strategy_id="s", intents=IntentJournal(), data=data),
    )
    event = RuntimeEnvelope(
        "market",
        "quote",
        now,
        1,
        Quote(
            instrument_id="instrument:spot:btc:usdt",
            market_id="market:binance:spot:btc_usdt",
            market_key="binance_spot_btc_usdt",
            time=now,
            bid=Decimal("100"),
            ask=Decimal("101"),
            source="binance",
        ),
    )

    pipeline.on_event(event)

    assert views.require("market.subscriptions").active_count == 1
    quote_window = views.require("market.window.binance_spot_btc_usdt.quotes")
    assert quote_window.latest.bid == Decimal("100")
    assert quote_window.size == 1
    assert views.require("market.windows").total_count == 1


def test_strategy_context_reads_market_views_through_typed_api() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    views = ViewStore()
    data = FakeMarketDataService()
    pipeline = RuntimePortPipeline(
        views=views,
        processors=runtime_processors(strategy_id="s", intents=IntentJournal(), data=data),
    )
    market = data.market

    pipeline.on_event(
        RuntimeEnvelope(
            "market",
            "quote",
            now,
            1,
            Quote(
                instrument_id=market.instrument_id,
                market_id=market.market_id,
                market_key=market.market_key,
                time=now,
                bid=Decimal("100"),
                ask=Decimal("101"),
                source="binance",
            ),
        )
    )
    pipeline.on_event(
        RuntimeEnvelope(
            "market",
            "bar",
            now,
            2,
            Bar(
                instrument_id=market.instrument_id,
                market_id=market.market_id,
                market_key=market.market_key,
                time=now,
                timeframe="1m",
                open=Decimal("100"),
                high=Decimal("102"),
                low=Decimal("99"),
                close=Decimal("101"),
                volume=Decimal("10"),
                source="binance",
            ),
        )
    )
    pipeline.on_event(
        RuntimeEnvelope(
            "market",
            "funding_rate",
            now,
            3,
            RateObservation(
                rate_id=str(market.market_id),
                market_id=market.market_id,
                instrument_id=market.instrument_id,
                time=now,
                rate=Decimal("0.0001"),
                basis="funding_rate",
                source="binance",
            ),
        )
    )
    pipeline.on_event(
        RuntimeEnvelope(
            "market",
            "option_greeks",
            now,
            4,
            OptionGreeks(
                instrument_id=market.instrument_id,
                market_id=market.market_id,
                market_key=market.market_key,
                time=now,
                delta=Decimal("0.52"),
                gamma=Decimal("0.01"),
                theta=Decimal("-0.05"),
                vega=Decimal("0.12"),
                implied_volatility=Decimal("0.65"),
                mark_price=Decimal("1200"),
                source="binance",
            ),
        )
    )

    context = RuntimeContext(strategy_id="s", views=views)
    quotes = context.market.quotes("BTC/USDT", exchange="binance", market_type="spot")
    bars = context.market.bars("BTC/USDT", timeframe="1m", exchange="binance", market_type="spot")
    rates = context.market.rates("BTC/USDT", basis="funding_rate", exchange="binance", market_type="spot")
    greeks = context.market.option_greeks("BTC/USDT", exchange="binance", market_type="spot")

    assert isinstance(quotes.latest, Quote)
    assert quotes.latest.ask == Decimal("101")
    assert isinstance(bars.latest, Bar)
    assert bars.latest.close == Decimal("101")
    assert isinstance(rates.latest, RateObservation)
    assert rates.latest.rate == Decimal("0.0001")
    assert isinstance(greeks.latest, OptionGreeks)
    assert greeks.latest.delta == Decimal("0.52")
    assert views.require("market.window.binance_spot_btc_usdt.option_greeks").latest.implied_volatility == Decimal("0.65")


def test_market_view_windows_keep_recent_history_and_orderbook_change() -> None:
    first = datetime(2026, 1, 1, tzinfo=timezone.utc)
    second = datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
    views = ViewStore()
    data = FakeMarketDataService()
    pipeline = RuntimePortPipeline(
        views=views,
        processors=runtime_processors(strategy_id="s", intents=IntentJournal(), data=data),
    )
    market = data.market

    for sequence, trade in (
        (
            1,
            TradePrint(
                instrument_id=market.instrument_id,
                market_id=market.market_id,
                market_key=market.market_key,
                time=first,
                trade_id="t1",
                price=Decimal("100"),
                size=Decimal("1"),
                source="binance",
            ),
        ),
        (
            2,
            TradePrint(
                instrument_id=market.instrument_id,
                market_id=market.market_id,
                market_key=market.market_key,
                time=second,
                trade_id="t2",
                price=Decimal("101"),
                size=Decimal("2"),
                source="binance",
            ),
        ),
    ):
        pipeline.on_event(RuntimeEnvelope("market", "trade", trade.time, sequence, trade))

    for sequence, book in (
        (
            3,
            OrderBookSnapshot(
                instrument_id=market.instrument_id,
                market_id=market.market_id,
                market_key=market.market_key,
                time=first,
                bids=(PriceLevel(Decimal("99"), Decimal("1")),),
                asks=(PriceLevel(Decimal("101"), Decimal("1")),),
                source="binance",
            ),
        ),
        (
            4,
            OrderBookSnapshot(
                instrument_id=market.instrument_id,
                market_id=market.market_id,
                market_key=market.market_key,
                time=second,
                bids=(PriceLevel(Decimal("100"), Decimal("2")),),
                asks=(PriceLevel(Decimal("101"), Decimal("3")),),
                source="binance",
            ),
        ),
    ):
        pipeline.on_event(RuntimeEnvelope("market", "orderbook", book.time, sequence, book))

    context = RuntimeContext(strategy_id="s", views=views)
    trades = context.market.trades("BTC/USDT", exchange="binance", market_type="spot")
    orderbooks = context.market.orderbooks("BTC/USDT", exchange="binance", market_type="spot")

    assert trades.size == 2
    assert trades.previous.price == Decimal("100")
    assert trades.latest.price == Decimal("101")
    assert orderbooks.size == 2
    assert orderbooks.current.bid1.price == Decimal("100")
    assert orderbooks.change.spread_change == Decimal("-1")


def test_account_port_publishes_current_account_view() -> None:
    views = ViewStore()
    account = FakeAccountService()
    pipeline = RuntimePortPipeline(
        views=views,
        processors=runtime_processors(strategy_id="s", intents=IntentJournal(), account=account),
    )

    pipeline.publish()

    current = views.require("account.current.paper.paper.main")
    assert current.context == account.context
    assert current.cash == Decimal("1000")
    assert current.source is AccountSource.SIMULATED


class FakeReferenceService:
    def __init__(self) -> None:
        self._catalog = ReferenceCatalog()

    def catalog(self) -> ReferenceCatalog:
        return self._catalog

    def resolver(self, *, as_of: datetime | None = None) -> MarketResolver:
        return MarketResolver(self._catalog, as_of=as_of) if as_of is not None else MarketResolver()

    def lifecycle_events(self) -> tuple[object, ...]:
        return ()


def test_reference_port_publishes_catalog_view() -> None:
    views = ViewStore()
    reference = FakeReferenceService()
    pipeline = RuntimePortPipeline(
        views=views,
        processors=runtime_processors(strategy_id="s", intents=IntentJournal(), reference=reference),
    )

    pipeline.publish()

    assert views.require("reference.catalog").market_count == 0
    assert views.registry.require("reference.catalog").evidence == "runtime reference catalog view state"


def test_kernel_wires_runtime_ports_to_view_states() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    context = AccountContext(AccountRef("paper", "main"), Environment.PAPER)
    execution = ExecutionCoordinator()
    execution.plan_order(
        cash_order_request(
            order_id="order-1",
            context=context,
            instrument_id="instrument:spot:btc:usdt",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
        ),
        at=now,
    )

    kernel = RuntimeKernel(
        EmptyStrategy(),
        data=FakeMarketDataService(),
        account=FakeAccountService(),
        reference=FakeReferenceService(),
        trading_execution=BacktestExecutionService(execution),
        execution_coordinator=execution,
    )
    kernel.start()

    assert kernel.views.require("market.subscriptions").active_count == 1
    assert kernel.views.require("account.current.paper.paper.main").cash == Decimal("1000")
    assert kernel.views.require("reference.catalog").market_count == 0
    assert kernel.views.require("execution.current").total_orders == 1


def test_system_risk_execution_and_order_views_are_business_panels() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    context = AccountContext(AccountRef("paper", "main"), Environment.PAPER)
    execution = ExecutionCoordinator()
    execution.plan_order(
        cash_order_request(
            order_id="order-1",
            context=context,
            instrument_id="instrument:spot:btc:usdt",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
        ),
        at=now,
    )
    views = ViewStore()
    pipeline = RuntimePortPipeline(
        views=views,
        processors=runtime_processors(
            strategy_id="s",
            intents=IntentJournal(),
            execution_coordinator=execution,
        ),
    )

    pipeline.on_event(RuntimeEnvelope("system", "risk.limit_warn", now, 1, {"limit": "gross"}))

    assert views.require("system.strategy").event_count == 1
    assert views.require("system.events").last_name == "risk.limit_warn"
    assert views.require("risk.events").last_payload == {"limit": "gross"}
    assert views.require("execution.current").total_orders == 1
    assert views.require("order.current").state.latest_order.order_id == "order-1"
