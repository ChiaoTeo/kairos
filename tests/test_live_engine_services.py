from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.runtime.ports import MarketDataSubscriptionSpec
from kairospy.application.service.modes.live import (
    LiveAccountService,
    LiveExecutionService,
    LiveMarketDataService,
    LiveTradingSafetyPolicy,
)
from kairospy.core.account import AccountContext, AccountRef, AccountSource, Environment
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.intent import IntentStatus, target_position_intent
from kairospy.core.market import Quote
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


class FakeBroker:
    def __init__(self, *, open_orders: tuple[Mapping[str, object], ...] = ()) -> None:
        self.created: list[dict[str, object]] = []
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
        return {"free": {"USDT": "1000"}, "used": {"USDT": "0"}, "total": {"USDT": "1000"}}

    def fetch_open_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> tuple[Mapping[str, object], ...]:
        return self.open_orders


class FakeLiveAccountStream:
    async def watch_balance(self, *, params: Mapping[str, object] | None = None) -> AsyncIterator[Mapping[str, object]]:
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

    async def close(self) -> None:
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


def test_ccxt_driver_normalizes_reference_symbol_for_live_ticker() -> None:
    exchange = _FakeAsyncExchange()
    driver = CcxtDriver(async_exchange_factory=lambda exchange_id: exchange)

    event = asyncio.run(_first(driver.watch_ticker("binance", SourceSymbol("ETH/USDT"), params={"max_events": 1})))  # type: ignore[arg-type]

    assert event["symbol"] == "ETH/USDT"
    assert exchange.symbols == ["ETH/USDT"]


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
    account = AccountContext(AccountRef("binance", "main"), Environment.LIVE)
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
    assert service.state(account.account).balances[0].total == Decimal("1000")  # type: ignore[union-attr]


def test_live_account_service_streams_private_balance_updates() -> None:
    account = AccountContext(AccountRef("binance", "main"), Environment.LIVE)
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
    assert service.snapshot(account.account).balances[0].total == Decimal("1010")  # type: ignore[union-attr]
    assert coordinator.ledger.cash(account.account)["USDT"] == Decimal("10")


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
    account = AccountContext(AccountRef("binance", "main"), Environment.LIVE)
    broker = FakeBroker()
    coordinator = ExecutionCoordinator(broker=broker, broker_symbol_resolver=lambda symbol: "ETH/USDT")
    account_service = LiveAccountService(account, coordinator, broker=broker)
    execution = LiveExecutionService(
        coordinator,
        account=account,
        snapshot_provider=account_service.snapshot,
        safety_policy=safety_policy,
    )
    return (
        RuntimeKernel(
            LiveTargetPositionStrategy(instrument_id=market.instrument_id, market_id=market.market_id, limit_price=limit_price),
            data=market_data,
            account=account_service,
            trading_execution=execution,
            execution_coordinator=coordinator,
        ),
        broker,
    )
