from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.runtime.orchestration.state import RuntimePorts, RuntimeStores
from kairospy.application.launch import LaunchAccountBinding, LaunchAccountDirectory
from kairospy.application.ports import MarketDataSubscriptionSpec
from kairospy.application.service.domain.account.routing import account_book_route
from kairospy.application.service.modes.live import (
    LiveAccountService,
    LiveExecutionService,
    LiveMarketDataService,
    LiveTradingSafetyPolicy,
)
from kairospy.application.service.runtime import RuntimeApplicationServices, RuntimeServiceDependencies
from kairospy.core.account import AccountBookKind, AccountContext, AccountBookRef, AccountSource, Environment
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.intent import IntentJournal, IntentStatus, target_position_intent
from kairospy.core.market import OptionGreeks, Quote
from kairospy.core.reference import MarketResolver, SourceSymbol
from kairospy.infrastructure.integrations.drivers import CcxtDriver


class LiveTargetPositionStrategy:
    strategy_id = "live-strategy"

    def __init__(self, *, instrument_id: str, market_id: str, limit_price: Decimal | None = None) -> None:
        self.instrument_id = instrument_id
        self.market_id = market_id
        self.limit_price = limit_price

    def on_start(self, context: object) -> None:
        return None

    def on_data(self, context: object, signal: object) -> None:
        context.intent(  # type: ignore[attr-defined]
            target_position_intent(
                strategy_id=self.strategy_id,
                instrument_id=self.instrument_id,
                market_id=self.market_id,
                target_quantity=Decimal("1"),
                at=signal.time,  # type: ignore[attr-defined]
                intent_id="live-intent-1",
                limit_price=self.limit_price,
            )
        )

    def on_intent(self, context: object, intent: object) -> None:
        return None

    def on_clock(self, context: object, signal: object) -> None:
        return None

    def on_system(self, context: object, signal: object) -> None:
        return None

    def on_end(self, context: object) -> None:
        return None


class FakeLiveFeed:
    def __init__(self, row: Mapping[str, object] | tuple[Mapping[str, object], ...]) -> None:
        self.rows = row if isinstance(row, tuple) else (row,)
        self.symbols: list[object] = []

    async def watch_ticker(self, symbol: str, *, params: Mapping[str, object] | None = None) -> AsyncIterator[Mapping[str, object]]:
        self.symbols.append(symbol)
        for row in self.rows:
            yield row

    async def watch_order_book(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        self.symbols.append(symbol)
        if False:
            yield {}

    async def watch_trades(
        self,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        self.symbols.append(symbol)
        if False:
            yield {}

    async def watch_option_greeks(
        self,
        symbol: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        self.symbols.append(symbol)
        for row in self.rows:
            yield row


class FakeBroker:
    def __init__(self, *, open_orders: tuple[Mapping[str, object], ...] = ()) -> None:
        self.created: list[dict[str, object]] = []
        self.balance_params: list[Mapping[str, object] | None] = []
        self.order_params: list[Mapping[str, object] | None] = []
        self.open_orders = open_orders

    def create_order(
        self,
        symbol: str,
        *,
        side: str,
        type: str,
        amount: object,
        price: object | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        self.created.append({"symbol": symbol, "side": side, "type": type, "amount": amount, "price": price, "params": params})
        return {"id": "venue-order-1"}

    def cancel_order(
        self,
        id: str,
        *,
        symbol: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        return {"id": id, "status": "canceled"}

    def fetch_balance(self, *, params: Mapping[str, object] | None = None) -> Mapping[str, object]:
        self.balance_params.append(params)
        return {"free": {"USDT": "1000"}, "used": {"USDT": "0"}, "total": {"USDT": "1000"}}

    def fetch_open_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> tuple[Mapping[str, object], ...]:
        self.order_params.append(params)
        return self.open_orders


class FakeLiveAccountStream:
    def __init__(self) -> None:
        self.balance_calls = 0

    async def watch_balance(self, *, params: Mapping[str, object] | None = None) -> AsyncIterator[Mapping[str, object]]:
        self.balance_calls += 1
        yield {
            "free": {"USDT": "1010"},
            "used": {"USDT": "0"},
            "total": {"USDT": "1010"},
            "type": "deposit",
        }

    async def watch_orders(
        self,
        symbol: str | None = None,
        *,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        if False:
            yield {}

    async def watch_my_trades(
        self,
        symbol: str | None = None,
        *,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        if False:
            yield {}


class _FakeAsyncExchange:
    def __init__(self) -> None:
        self.symbols: list[object] = []

    async def load_markets(self) -> Mapping[str, object]:
        return {}

    async def watch_ticker(self, symbol: str, params: Mapping[str, object] | None = None) -> Mapping[str, object]:
        self.symbols.append(symbol)
        return {"symbol": symbol, "timestamp": 1760000000000, "bid": "100", "ask": "101"}

    async def watch_greeks(self, symbol: str, params: Mapping[str, object] | None = None) -> Mapping[str, object]:
        self.symbols.append(symbol)
        return {"symbol": symbol, "timestamp": 1760000000000, "delta": "0.5", "markIV": "0.6"}

    async def close(self) -> None:
        return None


class _FakeBalanceExchange:
    def __init__(self) -> None:
        self.options: dict[str, object] = {}
        self.balance_params: Mapping[str, object] | None = None

    def fetch_balance(self, params: Mapping[str, object] | None = None) -> Mapping[str, object]:
        self.balance_params = params
        return {"total": {"USDT": "1"}}

    def close(self) -> None:
        return None


def test_live_market_service_streams_from_integration_feed() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    market = MarketResolver(default_venue="binance", default_market="spot").resolve("ETH/USDT")
    feed = FakeLiveFeed({"timestamp": int(now.timestamp() * 1000), "bid": "2000", "ask": "2001"})
    service = LiveMarketDataService(feed=feed, source_name="binance-live")
    service.subscribe(service_spec := MarketDataSubscriptionSpec(market, (Quote,)))

    event = asyncio.run(_first(service.events()))

    assert service_spec.key in {subscription.key for subscription in service.subscriptions()}
    assert feed.symbols == ["ETH/USDT"]
    assert event.domain == "market"
    assert event.kind == "quote"
    assert event.payload.value.ask == Decimal("2001")  # type: ignore[union-attr]
    assert service.view().source == "binance-live"


def test_live_market_service_streams_option_greeks_from_integration_feed() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    market = MarketResolver(default_venue="binance", default_market="option").resolve("BTC-260926-120000-C")
    feed = FakeLiveFeed(
        {
            "timestamp": int(now.timestamp() * 1000),
            "delta": "0.51",
            "gamma": "0.002",
            "theta": "-0.04",
            "vega": "0.11",
            "markIV": "0.62",
            "markPrice": "1234.5",
        }
    )
    service = LiveMarketDataService(feed=feed, source_name="binance-live")
    service.subscribe(MarketDataSubscriptionSpec(market, (OptionGreeks,)))

    event = asyncio.run(_first(service.events()))

    assert feed.symbols == ["BTC-260926-120000-C"]
    assert event.kind == "option_greeks"
    assert event.payload.value.delta == Decimal("0.51")  # type: ignore[union-attr]
    assert event.payload.value.implied_volatility == Decimal("0.62")  # type: ignore[union-attr]


def test_ccxt_driver_normalizes_reference_symbol_for_live_ticker() -> None:
    exchange = _FakeAsyncExchange()
    driver = CcxtDriver(async_exchange_factory=lambda exchange_id: exchange)

    event = asyncio.run(_first(driver.watch_ticker("binance", SourceSymbol("ETH/USDT"), params={"max_events": 1})))  # type: ignore[arg-type]

    assert event["symbol"] == "ETH/USDT"
    assert exchange.symbols == ["ETH/USDT"]


def test_ccxt_driver_streams_option_greeks() -> None:
    exchange = _FakeAsyncExchange()
    driver = CcxtDriver(async_exchange_factory=lambda exchange_id: exchange)

    event = asyncio.run(_first(driver.watch_option_greeks("binance", SourceSymbol("BTC-260926-120000-C"), params={"max_events": 1})))  # type: ignore[arg-type]

    assert event["symbol"] == "BTC-260926-120000-C"
    assert event["delta"] == "0.5"
    assert exchange.symbols == ["BTC-260926-120000-C"]


def test_ccxt_driver_limits_binance_balance_market_preload_to_spot() -> None:
    exchange = _FakeBalanceExchange()
    driver = CcxtDriver(exchange_factory=lambda exchange_id: exchange)

    balance = driver.fetch_balance("binance", params={"type": "margin", "marginMode": "isolated"})

    assert balance["total"]["USDT"] == "1"
    assert exchange.options["fetchMarkets"] == ["spot"]
    assert exchange.balance_params == {"type": "margin", "marginMode": "isolated"}


def test_live_market_service_stops_streaming_when_stop_requested() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    market = MarketResolver(default_venue="binance", default_market="spot").resolve("ETH/USDT")
    service = LiveMarketDataService(
        feed=FakeLiveFeed(
            (
                {"timestamp": int(now.timestamp() * 1000), "bid": "2000", "ask": "2001"},
                {"timestamp": int(now.timestamp() * 1000), "bid": "2002", "ask": "2003"},
            )
        ),
        source_name="binance-live",
    )
    service.subscribe(MarketDataSubscriptionSpec(market, (Quote,)))
    stop_after_first = {"seen": 0}
    service.set_stop_requested(lambda: stop_after_first["seen"] >= 1)

    events = asyncio.run(_collect_with_counter(service.events(), stop_after_first))

    assert len(events) == 1
    assert events[0].payload.value.ask == Decimal("2001")  # type: ignore[union-attr]


def test_live_execution_service_rejects_by_default() -> None:
    kernel, broker = _live_kernel(limit_price=Decimal("101"))

    asyncio.run(kernel.run())

    assert broker.created == []
    assert kernel.intents.get("live-intent-1").status is IntentStatus.REJECTED
    assert kernel.views.require("order.current").state.latest_order is None


def test_live_execution_service_submits_limit_order_when_enabled() -> None:
    kernel, broker = _live_kernel(
        limit_price=Decimal("101"),
        safety_policy=LiveTradingSafetyPolicy(trading_enabled=True, require_limit_orders=True, max_order_notional=Decimal("1000")),
    )

    asyncio.run(kernel.run())

    assert broker.created == [
        {
            "symbol": "ETH/USDT",
            "side": "buy",
            "type": "limit",
            "amount": Decimal("1"),
            "price": Decimal("101"),
            "params": None,
        }
    ]
    assert kernel.intents.get("live-intent-1").status is IntentStatus.ORDERING
    assert kernel.views.require("order.current").state.latest_order.status == "acknowledged"


def test_live_account_service_refreshes_from_broker_integration() -> None:
    account = AccountContext(AccountBookRef("binance", "main"), Environment.LIVE)
    coordinator = ExecutionCoordinator()
    service = LiveAccountService(
        account,
        coordinator,
        broker=FakeBroker(
            open_orders=(
                {
                    "id": "venue-order-1",
                    "symbol": "ETH/USDT",
                    "side": "buy",
                    "type": "limit",
                    "amount": "2",
                    "filled": "0",
                    "remaining": "2",
                    "price": "100",
                },
            )
        ),
    )

    snapshot = service.refresh(observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert snapshot.source is AccountSource.VENUE
    assert snapshot.balances[0].currency == "USDT"
    assert snapshot.open_orders[0].order_id == "venue-order-1"
    assert coordinator.orders.get("external:binance:main:venue-order-1").order_venue_id == "venue-order-1"
    assert service.state(account.book).balances[0].total == Decimal("1000")  # type: ignore[union-attr]


def test_account_book_route_maps_binance_books_to_ccxt_params() -> None:
    spot = account_book_route(AccountBookRef("binance", "main", AccountBookKind.SPOT), provider="binance")
    funding = account_book_route(AccountBookRef("binance", "main", AccountBookKind.FUNDING), provider="binance")
    futures = account_book_route(AccountBookRef("binance", "main", AccountBookKind.USD_M_FUTURES), provider="binance")
    isolated = account_book_route(AccountBookRef("binance", "main", AccountBookKind.ISOLATED_MARGIN, qualifier="ETH/USDT"), provider="binance")

    assert spot.balance_params == {}
    assert spot.order_params["defaultType"] == "spot"
    assert funding.balance_params["type"] == "funding"
    assert funding.can_trade is False
    assert futures.order_params["defaultType"] == "future"
    assert isolated.balance_params["marginMode"] == "isolated"
    assert isolated.balance_params["symbols"] == ["ETH/USDT"]


def test_live_account_service_refreshes_each_enabled_book_with_routed_params() -> None:
    spot = AccountContext(AccountBookRef("binance", "main", AccountBookKind.SPOT), Environment.LIVE)
    funding = AccountContext(AccountBookRef("binance", "main", AccountBookKind.FUNDING), Environment.LIVE)
    directory = LaunchAccountDirectory((LaunchAccountBinding("account1", 0, (spot, funding)),))
    routes = (
        account_book_route(spot.book, provider="binance"),
        account_book_route(funding.book, provider="binance"),
    )
    broker = FakeBroker(
        open_orders=(
            {
                "id": "venue-order-1",
                "symbol": "ETH/USDT",
                "side": "buy",
                "type": "limit",
                "amount": "2",
                "filled": "0",
                "remaining": "2",
                "price": "100",
            },
        )
    )
    service = LiveAccountService(spot, ExecutionCoordinator(), broker=broker, directory=directory, routes=routes)

    snapshots = service.refresh_all(observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert [snapshot.context for snapshot in snapshots] == [spot, funding]
    assert broker.balance_params == [
        {},
        {"type": "funding"},
    ]
    assert broker.order_params == [{"type": "spot", "defaultType": "spot"}]
    assert service.snapshot(funding.book).context == funding  # type: ignore[union-attr]


def test_live_execution_routes_target_position_to_intent_account_book_params() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    spot = AccountContext(AccountBookRef("binance", "main", AccountBookKind.SPOT), Environment.LIVE)
    swap = AccountContext(AccountBookRef("binance", "main", AccountBookKind.USD_M_FUTURES), Environment.LIVE)
    directory = LaunchAccountDirectory((LaunchAccountBinding("account1", 0, (spot, swap), ref="binance_main"),))
    routes = (
        account_book_route(spot.book, provider="binance"),
        account_book_route(swap.book, provider="binance"),
    )
    broker = FakeBroker()
    coordinator = ExecutionCoordinator(broker=broker, broker_symbol_resolver=lambda symbol: "ETH/USDT")
    service = LiveExecutionService(
        coordinator,
        account=spot,
        safety_policy=LiveTradingSafetyPolicy(trading_enabled=True, require_limit_orders=True),
        directory=directory,
        routes=routes,
    )
    context = _IntentContext(now)
    intent = target_position_intent(
        strategy_id="live-strategy",
        instrument_id="binance:usd_m_futures:ETH/USDT",
        market_id="binance:usd_m_futures:ETH/USDT",
        account_id="account1",
        account_book=AccountBookKind.USD_M_FUTURES,
        target_quantity=Decimal("1"),
        limit_price=Decimal("100"),
        at=now,
        intent_id="live-book-intent",
    )
    context.intents.record_intent(intent, at=now)

    assert service.execute_intent(intent, context) is True

    assert broker.created[0]["params"] == {"type": "future", "defaultType": "future"}
    assert coordinator.orders.get("live-book-intent-live-order").request.context == swap


def test_live_execution_routes_target_position_to_distinct_account_broker() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    binance_spot = AccountContext(AccountBookRef("binance", "main", AccountBookKind.SPOT), Environment.LIVE)
    okx_swap = AccountContext(AccountBookRef("okx", "hedge", "swap"), Environment.LIVE)
    directory = LaunchAccountDirectory(
        (
            LaunchAccountBinding("cash", 0, (binance_spot,), ref="binance_main"),
            LaunchAccountBinding("hedge", 1, (okx_swap,), ref="okx_hedge"),
        )
    )
    binance_broker = FakeBroker()
    okx_broker = FakeBroker()
    coordinator = ExecutionCoordinator(
        broker=binance_broker,
        broker_resolver=lambda account: {
            binance_spot.book: binance_broker,
            okx_swap.book: okx_broker,
        }.get(account),
        broker_symbol_resolver=lambda symbol: "ETH/USDT",
    )
    service = LiveExecutionService(
        coordinator,
        account=binance_spot,
        safety_policy=LiveTradingSafetyPolicy(trading_enabled=True, require_limit_orders=True),
        directory=directory,
        routes=(account_book_route(binance_spot.book, provider="binance"), account_book_route(okx_swap.book, provider="okx")),
    )
    context = _IntentContext(now)
    intent = target_position_intent(
        strategy_id="live-strategy",
        instrument_id="okx:swap:ETH/USDT",
        market_id="okx:swap:ETH/USDT",
        account_id="hedge",
        account_book="swap",
        target_quantity=Decimal("1"),
        limit_price=Decimal("100"),
        at=now,
        intent_id="live-okx-intent",
    )
    context.intents.record_intent(intent, at=now)

    assert service.execute_intent(intent, context) is True

    assert binance_broker.created == []
    assert okx_broker.created[0]["params"] == {"type": "swap", "defaultType": "swap"}


def test_live_account_service_streams_private_balance_updates() -> None:
    account = AccountContext(AccountBookRef("binance", "main"), Environment.LIVE)
    coordinator = ExecutionCoordinator()
    service = LiveAccountService(
        account,
        coordinator,
        broker=FakeBroker(),
        stream=FakeLiveAccountStream(),
        max_balance_events=1,
    )

    events = asyncio.run(_collect(service.events(), 2))

    assert [event.domain for event in events] == ["account", "account"]
    assert service.snapshot(account.book).balances[0].total == Decimal("1010")  # type: ignore[union-attr]
    assert coordinator.ledger.cash(account.book)["USDT"] == Decimal("10")


def test_live_account_service_streams_private_updates_for_each_account_book() -> None:
    spot = AccountContext(AccountBookRef("binance", "main", AccountBookKind.SPOT), Environment.LIVE)
    swap = AccountContext(AccountBookRef("okx", "hedge", "swap"), Environment.LIVE)
    directory = LaunchAccountDirectory((LaunchAccountBinding("cash", 0, (spot,)), LaunchAccountBinding("hedge", 1, (swap,))))
    spot_stream = FakeLiveAccountStream()
    swap_stream = FakeLiveAccountStream()
    service = LiveAccountService(
        spot,
        ExecutionCoordinator(),
        broker=FakeBroker(),
        directory=directory,
        routes=(account_book_route(spot.book, provider="binance"), account_book_route(swap.book, provider="okx")),
        stream_resolver=lambda account: {
            spot.book: spot_stream,
            swap.book: swap_stream,
        }.get(account),
        max_balance_events=1,
    )

    events = asyncio.run(_collect(service.events(), 4))

    assert [event.domain for event in events] == ["account", "account", "account", "account"]
    assert spot_stream.balance_calls == 1
    assert swap_stream.balance_calls == 1
    assert service.snapshot(spot.book).balances[0].total == Decimal("1010")  # type: ignore[union-attr]
    assert service.snapshot(swap.book).balances[0].total == Decimal("1010")  # type: ignore[union-attr]


async def _first(events: AsyncIterator[object]) -> object:
    async for event in events:
        await events.aclose()
        return event
    raise AssertionError("event stream was empty")


async def _collect(events: AsyncIterator[object], count: int) -> tuple[object, ...]:
    values = []
    async for event in events:
        values.append(event)
        if len(values) >= count:
            await events.aclose()
            break
    return tuple(values)


async def _collect_with_counter(events: AsyncIterator[object], counter: dict[str, int]) -> tuple[object, ...]:
    values = []
    async for event in events:
        values.append(event)
        counter["seen"] += 1
    return tuple(values)


class _IntentContext:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.intents = IntentJournal()

    def view(self, key: str, default: object = None) -> object:
        return default

    @property
    def market(self) -> object:
        return object()

    def latest_data(self, *, domain: str | None = None, kind: str | None = None) -> object | None:
        return None


def _live_kernel(
    *,
    limit_price: Decimal | None,
    safety_policy: LiveTradingSafetyPolicy | None = None,
) -> tuple[RuntimeKernel, FakeBroker]:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    market = MarketResolver(default_venue="binance", default_market="spot").resolve("ETH/USDT")
    market_data = LiveMarketDataService(
        feed=FakeLiveFeed({"timestamp": int(now.timestamp() * 1000), "bid": "100", "ask": "101"}),
        source_name="binance-live",
    )
    market_data.subscribe(MarketDataSubscriptionSpec(market, (Quote,)))
    account = AccountContext(AccountBookRef("binance", "main"), Environment.LIVE)
    broker = FakeBroker()
    coordinator = ExecutionCoordinator(broker=broker, broker_symbol_resolver=lambda symbol: "ETH/USDT")
    account_service = LiveAccountService(account, coordinator, broker=broker)
    execution = LiveExecutionService(
        coordinator,
        account=account,
        snapshot_provider=account_service.snapshot,
        safety_policy=safety_policy,
    )
    intents = IntentJournal()
    return (
        RuntimeKernel(
            LiveTargetPositionStrategy(instrument_id=market.instrument_id, market_id=market.market_id, limit_price=limit_price),
            ports=RuntimePorts(data=market_data, account=account_service, trading_execution=execution),
            stores=RuntimeStores(intents=intents),
            services=RuntimeApplicationServices.from_dependencies(
                RuntimeServiceDependencies(
                    intents=intents,
                    data=market_data,
                    account_snapshot_store=account_service,
                    account=account_service,
                    trading_execution=execution,
                    execution=coordinator,
                    fills_source=execution,
                )
            ),
        ),
        broker,
    )
