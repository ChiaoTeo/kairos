from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from kairospy.application.runtime.orchestration.pipeline import RuntimeProjectionPipeline
from kairospy.application.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.runtime.orchestration.state import RuntimePorts, RuntimeStores
from kairospy.application.runtime.dispatch.context import RuntimeContext
from kairospy.application.ports import LaunchScopedAccountPort
from kairospy.application.launch import LaunchAccountBinding, LaunchAccountDirectory
from kairospy.application.runtime.processors.account import AccountCurrentViewState
from kairospy.application.runtime.processors.system import runtime_processors
from kairospy.application.protocol import RuntimeEnvelope
from kairospy.application.ports import DataSubscription, MarketDataSubscriptionSpec
from kairospy.application.service.runtime import RuntimeAccountService, RuntimeAccountViewProjectionService, RuntimeApplicationServices, RuntimeServiceDependencies
from kairospy.application.service.modes.backtest import BacktestExecutionService
from kairospy.application.service.domain.reference import catalog_from_market_rows
from kairospy.core.account import AccountBalance, AccountBookKind, AccountCapability, AccountContext, AccountFeeSchedule, AccountRef, AccountSnapshot, AccountSource, AccountState, Environment
from kairospy.core.execution import ExecutionCoordinator, cash_order_request
from kairospy.core.intent import IntentJournal
from kairospy.core.market import Bar, OptionGreeks, OrderBookSnapshot, PriceLevel, Quote, RateObservation, TradePrint
from kairospy.core.order import OrderSide
from kairospy.core.reference import LifecycleEvent, LifecycleEventType, MarketRef, MarketResolver, ReferenceCatalog
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

    def directory(self) -> LaunchAccountDirectory:
        return LaunchAccountDirectory.from_contexts((self.context,))

    def capabilities(self, account: AccountRef | None = None) -> tuple[AccountCapability, ...]:
        return ()

    def fees(self, account: AccountRef | None = None) -> tuple[AccountFeeSchedule, ...]:
        return ()

    def snapshot(self, account: AccountRef | None = None) -> AccountSnapshot | None:
        return self._snapshot

    def state(self, account: AccountRef | None = None) -> AccountState | None:
        return self._state

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        self._snapshot = snapshot
        self._state = AccountState(
            snapshot.context,
            snapshot.balances,
            snapshot.margins,
            snapshot.positions,
            snapshot.open_orders,
            snapshot.observed_at,
            snapshot.source,
            liabilities=snapshot.liabilities,
        )

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def view(self) -> dict[str, object]:
        return {}


class MutableFakeAccountService(FakeAccountService):
    def __init__(self) -> None:
        super().__init__()
        self.update_count = 0

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        self.update_count += 1
        self._snapshot = snapshot
        self._state = AccountState(
            snapshot.context,
            snapshot.balances,
            snapshot.margins,
            snapshot.positions,
            snapshot.open_orders,
            snapshot.observed_at,
            snapshot.source,
            liabilities=snapshot.liabilities,
        )


class ExplodingSnapshotUpdateAccountService(FakeAccountService):
    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        raise AssertionError("view state must not update account snapshots")


class FakeMultiAccountService:
    def __init__(self) -> None:
        self.spot = AccountContext(AccountRef("binance", "main", AccountBookKind.SPOT), Environment.PAPER)
        self.perp = AccountContext(AccountRef("okx", "hedge", "swap"), Environment.PAPER)
        self._states = {
            self.spot.account: AccountState(
                self.spot,
                (AccountBalance.from_total_locked("USDT", Decimal("1000"), Decimal("0"), source=AccountSource.SIMULATED),),
                (),
                (),
                (),
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                AccountSource.SIMULATED,
            ),
            self.perp.account: AccountState(
                self.perp,
                (AccountBalance.from_total_locked("USDT", Decimal("500"), Decimal("0"), source=AccountSource.SIMULATED),),
                (),
                (),
                (),
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                AccountSource.SIMULATED,
            ),
        }

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        if False:
            yield

    def accounts(self) -> tuple[AccountContext, ...]:
        return (self.spot, self.perp)

    def directory(self) -> LaunchAccountDirectory:
        return LaunchAccountDirectory.from_contexts(self.accounts())

    def capabilities(self, account: AccountRef | None = None) -> tuple[AccountCapability, ...]:
        return ()

    def snapshot(self, account: AccountRef | None = None) -> AccountSnapshot | None:
        state = self.state(account)
        if state is None:
            return None
        return AccountSnapshot(
            state.context,
            balances=state.balances,
            margins=state.margins,
            positions=state.positions,
            open_orders=state.open_orders,
            observed_at=state.observed_at,
            source=state.source,
        )

    def state(self, account: AccountRef | None = None) -> AccountState | None:
        if account is None:
            return None
        return self._states.get(account)

    def fees(self, account: AccountRef | None = None) -> tuple[AccountFeeSchedule, ...]:
        fees = (
            AccountFeeSchedule(self.spot.account, Decimal("0.001"), Decimal("0.0015")),
            AccountFeeSchedule(self.perp.account, Decimal("0.0002"), Decimal("0.0005")),
        )
        if account is None:
            return fees
        return tuple(item for item in fees if item.book == account)

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        self._states[snapshot.context.account] = AccountState(
            snapshot.context,
            snapshot.balances,
            snapshot.margins,
            snapshot.positions,
            snapshot.open_orders,
            snapshot.observed_at,
            snapshot.source,
            liabilities=snapshot.liabilities,
        )


def test_market_view_state_publishes_business_views() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    views = ViewStore()
    data = FakeMarketDataService()
    intents = IntentJournal()
    services = RuntimeApplicationServices.from_dependencies(RuntimeServiceDependencies(intents=intents, data=data))
    pipeline = RuntimeProjectionPipeline(
        views=views,
        processors=runtime_processors(strategy_id="s", intents=intents, services=services),
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
    schema = views.registry.require("market.window.binance_spot_btc_usdt.quotes")
    assert schema.evidence == "runtime market quotes window state"


def test_strategy_context_reads_market_views_through_typed_api() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    views = ViewStore()
    data = FakeMarketDataService()
    intents = IntentJournal()
    services = RuntimeApplicationServices.from_dependencies(RuntimeServiceDependencies(intents=intents, data=data))
    pipeline = RuntimeProjectionPipeline(
        views=views,
        processors=runtime_processors(strategy_id="s", intents=intents, services=services),
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
    intents = IntentJournal()
    services = RuntimeApplicationServices.from_dependencies(RuntimeServiceDependencies(intents=intents, data=data))
    pipeline = RuntimeProjectionPipeline(
        views=views,
        processors=runtime_processors(strategy_id="s", intents=intents, services=services),
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
    intents = IntentJournal()
    services = RuntimeApplicationServices.from_dependencies(RuntimeServiceDependencies(intents=intents, account=account))
    pipeline = RuntimeProjectionPipeline(
        views=views,
        processors=runtime_processors(strategy_id="s", intents=intents, services=services),
    )

    pipeline.publish()

    current = views.require("account.current.paper.paper.main")
    assert current.context == account.context
    assert current.cash == Decimal("1000")
    assert current.source is AccountSource.SIMULATED


def test_account_processor_ingests_snapshot_before_publishing_view() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    views = ViewStore()
    account = MutableFakeAccountService()
    intents = IntentJournal()
    services = RuntimeApplicationServices.from_dependencies(
        RuntimeServiceDependencies(intents=intents, account_snapshot_store=account, account=account)
    )
    pipeline = RuntimeProjectionPipeline(
        views=views,
        processors=runtime_processors(strategy_id="s", intents=intents, services=services),
    )
    snapshot = AccountSnapshot(
        account.context,
        balances=(AccountBalance.from_total_locked("USDT", Decimal("1250"), Decimal("0"), source=AccountSource.VENUE),),
        observed_at=now,
        source=AccountSource.VENUE,
    )

    pipeline.on_event(RuntimeEnvelope("account", "snapshot", now, 1, snapshot))

    current = views.require("account.current.paper.paper.main")
    assert account.update_count == 1
    assert current.event_count == 1
    assert current.cash == Decimal("1250")
    assert current.source is AccountSource.VENUE


def test_runtime_processors_can_use_application_services_for_snapshot_ingestion() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    views = ViewStore()
    account = MutableFakeAccountService()
    intents = IntentJournal()
    services = RuntimeApplicationServices.from_dependencies(
        RuntimeServiceDependencies(
            intents=intents,
            account_snapshot_store=account,
            account=account,
        )
    )
    pipeline = RuntimeProjectionPipeline(
        views=views,
        processors=runtime_processors(
            strategy_id="s",
            intents=intents,
            services=services,
        ),
    )
    snapshot = AccountSnapshot(
        account.context,
        balances=(AccountBalance.from_total_locked("USDT", Decimal("1300"), Decimal("0"), source=AccountSource.VENUE),),
        observed_at=now,
        source=AccountSource.VENUE,
    )

    pipeline.on_event(RuntimeEnvelope("account", "snapshot", now, 1, snapshot))

    assert account.update_count == 1
    assert views.require("account.current.paper.paper.main").cash == Decimal("1300")


def test_account_current_view_state_does_not_update_snapshots() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    account = ExplodingSnapshotUpdateAccountService()
    state = AccountCurrentViewState(RuntimeAccountService(views=RuntimeAccountViewProjectionService(account)), account.context)
    snapshot = AccountSnapshot(
        account.context,
        balances=(AccountBalance.from_total_locked("USDT", Decimal("1250"), Decimal("0"), source=AccountSource.VENUE),),
        observed_at=now,
        source=AccountSource.VENUE,
    )

    state.on_event(RuntimeEnvelope("account", "snapshot", now, 1, snapshot))

    assert state.view().event_count == 1
    assert state.view().cash == Decimal("1000")


def test_launch_account_directory_resolves_alias_index_and_book() -> None:
    spot = AccountContext(AccountRef("binance", "main", AccountBookKind.SPOT), Environment.PAPER)
    funding = AccountContext(AccountRef("binance", "main", AccountBookKind.FUNDING), Environment.PAPER)
    directory = LaunchAccountDirectory((LaunchAccountBinding("account1", 0, (spot, funding), ref="binance_main"),))

    assert directory.require("account1").require_book(AccountBookKind.SPOT) == spot
    assert directory.require(0).require_book("funding") == funding
    assert directory.contexts() == (spot, funding)


def test_launch_scoped_account_port_publishes_launch_aliases() -> None:
    views = ViewStore()
    account = FakeMultiAccountService()
    directory = LaunchAccountDirectory(
        (
            LaunchAccountBinding("account1", 0, (account.spot,), ref="binance_main"),
            LaunchAccountBinding("account2", 1, (account.perp,), ref="okx_main"),
        )
    )
    scoped = LaunchScopedAccountPort(account, directory)
    intents = IntentJournal()
    services = RuntimeApplicationServices.from_dependencies(RuntimeServiceDependencies(intents=intents, account=scoped))
    pipeline = RuntimeProjectionPipeline(
        views=views,
        processors=runtime_processors(strategy_id="s", intents=intents, services=services),
    )

    pipeline.publish()
    context = RuntimeContext(strategy_id="s", views=views)
    books = views.require("account.books").books

    assert {book.account_alias for book in books} == {"account1", "account2"}
    assert {book.account_index for book in books} == {0, 1}
    assert context.account("account1").book(AccountBookKind.SPOT).cash == Decimal("1000")
    assert context.account(1).book("swap").cash == Decimal("500")


def test_strategy_context_reads_multiple_account_books_through_typed_api() -> None:
    views = ViewStore()
    account = FakeMultiAccountService()
    intents = IntentJournal()
    services = RuntimeApplicationServices.from_dependencies(RuntimeServiceDependencies(intents=intents, account=account))
    pipeline = RuntimeProjectionPipeline(
        views=views,
        processors=runtime_processors(strategy_id="s", intents=intents, services=services),
    )

    pipeline.publish()
    context = RuntimeContext(strategy_id="s", views=views)

    books = views.require("account.books")
    assert books.total_count == 2
    capabilities = views.require("account.capabilities")
    assert capabilities.total_count == 2
    fees = views.require("account.fees")
    assert fees.total_count == 2
    portfolio = views.require("account.portfolio.paper.binance.main")
    assert portfolio.account_key == "binance.main"
    assert portfolio.cash == Decimal("1000")
    assert context.account("binance.main").book("spot").cash == Decimal("1000")
    assert context.account(0).book(AccountBookKind.SPOT).cash == Decimal("1000")
    current = context.account(0).book(AccountBookKind.SPOT)
    detail = context.account(0).detail(AccountBookKind.SPOT)
    assert not hasattr(current, "account_state")
    assert not hasattr(current, "snapshot")
    assert detail.account_state is not None
    assert detail.snapshot is not None
    assert context.account(0).overview().cash == Decimal("1000")
    assert context.account(0).fees(book=AccountBookKind.SPOT)[0].taker == Decimal("0.0015")
    assert context.account("binance.main").fees()[0].maker == Decimal("0.001")
    assert context.accounts.fees(account="binance.main.spot")[0].maker == Decimal("0.001")
    assert context.accounts.book("binance.main.spot").cash == Decimal("1000")
    assert context.accounts.book("swap").cash == Decimal("500")
    assert context.accounts.balance("USDT", account="binance.main.spot").total == Decimal("1000")
    with pytest.raises(ValueError, match="multiple account views"):
        context.account()


def test_runtime_context_target_position_records_account_book() -> None:
    context = RuntimeContext(strategy_id="s")

    intent = context.target_position("binance:spot:ETH/USDT", "1", account="account1", book=AccountBookKind.SPOT, intent_id="intent-1")

    assert str(intent.account_id) == "account1"
    assert intent.account_book == "spot"
    assert context.intents.get("intent-1").intent.account_book == "spot"  # type: ignore[attr-defined]


def test_runtime_context_target_position_prefers_reference_views() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    views = ViewStore()
    intents = IntentJournal()
    reference = FakePopulatedReferenceService(as_of=as_of)
    services = RuntimeApplicationServices.from_dependencies(RuntimeServiceDependencies(intents=intents, reference=reference))
    pipeline = RuntimeProjectionPipeline(
        views=views,
        processors=runtime_processors(
            strategy_id="s",
            intents=intents,
            services=services,
        ),
    )
    pipeline.processors.publish_views(views, as_of=as_of)
    context = RuntimeContext(strategy_id="s", views=views)

    intent = context.target_position("BTC/USDT", "1", intent_id="intent-reference")

    assert str(intent.market_id) == "market:binance:spot:btc_usdt"
    assert str(intent.instrument_id) == "instrument:spot:btc:usdt"


def test_strategy_account_reader_rejects_ambiguous_book_kind() -> None:
    views = ViewStore()
    account = FakeMultiAccountService()
    account.perp = AccountContext(AccountRef("okx", "hedge", AccountBookKind.SPOT), Environment.PAPER)
    account._states = {
        account.spot.account: account._states[account.spot.account],
        account.perp.account: AccountState(
            account.perp,
            (AccountBalance.from_total_locked("USDT", Decimal("500"), Decimal("0"), source=AccountSource.SIMULATED),),
            (),
            (),
            (),
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            AccountSource.SIMULATED,
        ),
    }
    intents = IntentJournal()
    services = RuntimeApplicationServices.from_dependencies(RuntimeServiceDependencies(intents=intents, account=account))
    pipeline = RuntimeProjectionPipeline(
        views=views,
        processors=runtime_processors(strategy_id="s", intents=intents, services=services),
    )

    pipeline.publish()
    context = RuntimeContext(strategy_id="s", views=views)

    with pytest.raises(ValueError, match="multiple account books match account key: spot"):
        context.accounts.book("spot")
    assert context.account("binance.main").book("spot").cash == Decimal("1000")
    assert context.account("okx.hedge").book("spot").cash == Decimal("500")
    assert context.accounts.book("okx.hedge.spot").cash == Decimal("500")


class FakeReferenceService:
    def __init__(self) -> None:
        self._catalog = ReferenceCatalog()
        self._events: tuple[LifecycleEvent, ...] = ()

    def catalog(self) -> ReferenceCatalog:
        return self._catalog

    def resolver(self, *, as_of: datetime | None = None) -> MarketResolver:
        return MarketResolver(self._catalog, as_of=as_of) if as_of is not None else MarketResolver()

    def lifecycle_events(self) -> tuple[LifecycleEvent, ...]:
        return self._events


class FakePopulatedReferenceService(FakeReferenceService):
    def __init__(self, *, as_of: datetime) -> None:
        self._catalog = catalog_from_market_rows(
            (
                {
                    "venue": "binance",
                    "market": "spot",
                    "source_symbol": "BTC/USDT",
                    "base": "BTC",
                    "quote": "USDT",
                    "status": "trading",
                    "price_precision": 2,
                },
            ),
            effective_from=as_of,
        )
        market = self._catalog.list_markets(at=as_of)[0]
        self._events: tuple[LifecycleEvent, ...] = (
            LifecycleEvent(
                LifecycleEventType.LISTED,
                as_of,
                instrument_id=market.instrument_id,
                listing_id=market.listing_id,
                market_id=market.market_id,
                venue=market.venue,
                source_symbol=market.source_symbol,
            ),
        )


def test_reference_port_publishes_catalog_view() -> None:
    views = ViewStore()
    reference = FakeReferenceService()
    intents = IntentJournal()
    services = RuntimeApplicationServices.from_dependencies(RuntimeServiceDependencies(intents=intents, reference=reference))
    pipeline = RuntimeProjectionPipeline(
        views=views,
        processors=runtime_processors(strategy_id="s", intents=intents, services=services),
    )

    pipeline.publish()

    assert views.require("reference.catalog").market_count == 0
    assert views.registry.require("reference.catalog").evidence == "runtime reference catalog view state"


def test_reference_port_publishes_market_resolution_views() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    views = ViewStore()
    reference = FakePopulatedReferenceService(as_of=as_of)
    intents = IntentJournal()
    services = RuntimeApplicationServices.from_dependencies(RuntimeServiceDependencies(intents=intents, reference=reference))
    pipeline = RuntimeProjectionPipeline(
        views=views,
        processors=runtime_processors(strategy_id="s", intents=intents, services=services),
    )

    pipeline.processors.publish_views(views, as_of=as_of)
    context = RuntimeContext(strategy_id="s", views=views)

    catalog = context.reference.catalog()
    markets = context.reference.markets(venue="binance", market="spot", active_only=True)
    resolved = context.reference.market("BTC/USDT", venue="binance", market="spot")
    events = context.reference.lifecycle_events()

    assert catalog.market_count == 1
    assert catalog.active_market_count == 1
    assert markets.total_count == 1
    assert markets.markets[0].market_key == "binance_spot_btc_usdt"
    assert context.reference.resolve("BTC/USDT", venue="binance", market="spot") == resolved.ref
    assert resolved.instrument is not None
    assert resolved.base_asset is not None
    assert str(resolved.base_asset.symbol) == "BTC"
    assert events.total_count == 1
    assert events.latest.event_type == LifecycleEventType.LISTED.value


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

    account = FakeAccountService()
    data = FakeMarketDataService()
    reference = FakeReferenceService()
    trading_execution = BacktestExecutionService(execution)
    intents = IntentJournal()
    kernel = RuntimeKernel(
        EmptyStrategy(),
        ports=RuntimePorts(
            data=data,
            account=account,
            reference=reference,
            trading_execution=trading_execution,
        ),
        services=RuntimeApplicationServices.from_dependencies(
            RuntimeServiceDependencies(
                intents=intents,
                data=data,
                account=account,
                reference=reference,
                trading_execution=trading_execution,
                execution=execution,
                fills_source=trading_execution,
            )
        ),
    )
    kernel.start()

    assert kernel.views.require("market.subscriptions").active_count == 1
    assert kernel.views.require("account.current.paper.paper.main").cash == Decimal("1000")
    assert kernel.views.require("reference.catalog").market_count == 0
    assert kernel.views.require("execution.current").total_orders == 1


def test_kernel_accepts_runtime_stores_and_application_services() -> None:
    views = ViewStore()
    account = MutableFakeAccountService()
    stores = RuntimeStores(intents=IntentJournal(), views=views)
    ports = RuntimePorts(account=account)
    services = RuntimeApplicationServices.from_dependencies(
        RuntimeServiceDependencies(
            intents=stores.intents,
            account_snapshot_store=account,
            account=account,
        )
    )

    kernel = RuntimeKernel(
        EmptyStrategy(),
        ports=ports,
        stores=stores,
        services=services,
    )
    kernel.start()

    assert kernel.stores is stores
    assert kernel.intents is stores.intents
    assert kernel.views is views
    assert kernel.ports is ports
    assert kernel.services is services
    assert views.require("account.current.paper.paper.main").cash == Decimal("1000")

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
    intents = IntentJournal()
    services = RuntimeApplicationServices.from_dependencies(
        RuntimeServiceDependencies(intents=intents, execution=execution)
    )
    pipeline = RuntimeProjectionPipeline(
        views=views,
        processors=runtime_processors(
            strategy_id="s",
            intents=intents,
            services=services,
        ),
    )

    pipeline.on_event(RuntimeEnvelope("system", "risk.limit_warn", now, 1, {"limit": "gross"}))

    assert views.require("system.strategy").event_count == 1
    assert views.require("system.events").last_name == "risk.limit_warn"
    assert views.require("risk.events").last_payload == {"limit": "gross"}
    assert views.require("execution.current").total_orders == 1
    assert views.require("order.current").state.latest_order.order_id == "order-1"
