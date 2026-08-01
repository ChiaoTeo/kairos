from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.usecases.market.subscriptions import MarketDataSubscriptionSpec
from kairospy.application.support.runtime.services.market.streaming import StreamingMarketDataService
from kairospy.application.support.system.resources.connections import DefaultConnectionManager
from kairospy.core.market import MarketEvent, MarketSubject, Quote
from kairospy.core.reference import MarketRef


class ManagedResource:
    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0

    def start(self) -> None:
        self.starts += 1

    def stop(self) -> None:
        self.stops += 1


def test_connection_manager_reuses_resources_and_reconnects_with_factory() -> None:
    manager = DefaultConnectionManager()
    created: list[ManagedResource] = []

    def factory() -> ManagedResource:
        resource = ManagedResource()
        created.append(resource)
        return resource

    first = manager.resolve("feed:binance", role="market_feed", factory=factory)
    second = manager.resolve("feed:binance", role="market_feed", factory=factory)

    assert first is second
    assert len(created) == 1

    manager.start()
    assert created[0].starts == 1

    replacement = manager.reconnect("feed:binance")

    assert replacement is created[1]
    assert created[0].stops == 1
    assert created[1].starts == 1
    assert manager.health()["connections"] == 1


class AsyncCloseResource:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def _reconnect_inside_running_loop(manager: DefaultConnectionManager) -> None:
    manager.reconnect("async")
    await asyncio.sleep(0)


def test_connection_manager_reconnect_handles_async_close_inside_running_loop() -> None:
    manager = DefaultConnectionManager()
    resources = [AsyncCloseResource(), AsyncCloseResource()]
    manager.resolve("async", role="market_feed", factory=lambda: resources.pop(0))

    asyncio.run(_reconnect_inside_running_loop(manager))

    assert resources == []


class FailingFeed:
    async def watch_ticker_updates(self, symbol: str, *, market: MarketRef, params: Mapping[str, object] | None = None) -> AsyncIterator[MarketEvent]:
        _ = symbol, market, params
        raise RuntimeError("stream disconnected")
        if False:
            yield


class WorkingFeed:
    async def watch_ticker_updates(self, symbol: str, *, market: MarketRef, params: Mapping[str, object] | None = None) -> AsyncIterator[MarketEvent]:
        _ = params
        quote = Quote(
            instrument_id=market.instrument_id,
            market_id=market.market_id,
            market_key=market.market_key,
            time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            bid=Decimal("100"),
            ask=Decimal("101"),
            source=symbol,
        )
        yield MarketEvent(MarketSubject("market", market.market_id), quote.time, quote)


async def _collect(service: StreamingMarketDataService) -> list[object]:
    return [event async for event in service.events()]


def test_streaming_market_data_reconnects_cached_feed_after_stream_error() -> None:
    manager = DefaultConnectionManager()
    feeds = [FailingFeed(), WorkingFeed()]

    def resolver(spec: MarketDataSubscriptionSpec) -> object:
        _ = spec
        return feeds.pop(0)

    service = StreamingMarketDataService(
        feed_resolver=resolver,
        source_name="test",
        connections=manager,
    )
    market = MarketRef.ephemeral(venue="binance", market="spot", source_symbol="BTC/USDT")
    service.subscribe(MarketDataSubscriptionSpec(market, (Quote,)))

    manager.start()
    events = asyncio.run(_collect(service))

    assert len(events) == 1
    assert events[0].payload.value.ask == Decimal("101")
    health = manager.health()
    assert health["status"] == "ready"
    assert health["items"][0]["reconnects"] == 1
    assert health["items"][0]["errors"] == 1
