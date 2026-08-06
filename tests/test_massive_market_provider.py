from __future__ import annotations

import asyncio
import json
from datetime import timezone
from decimal import Decimal

from kairospy.application.usecases.market.application.runtime import build_live_market
from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.usecases.market.application.requests import MarketDataSubscriptionSpec
from kairospy.domain.market import Quote
from kairospy.domain.reference import MarketRef
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec, RuntimeMode
from kairospy.application.usecases.market.application.integration import MarketFeedSubscriptionRequest
from kairospy.infrastructure.integrations.domain import AccessScope, AssetType, IntegrationRoute, ProviderId, ProviderRef, ProductFamily, TransportKind
from kairospy.infrastructure.integrations.services.gateways.massive.market import (
    MassiveOptionsMarketStreamConnection,
    MassiveStockMarketStreamConnection,
)
from kairospy.infrastructure.integrations.services.drivers.websocket import WebSocketDriver
from kairospy.infrastructure.integrations.services.gateways.massive.normalizers import MassiveStockNormalizers


class _Session:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()

    async def send(self, message: str) -> None:
        self.messages.append(json.loads(message))

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.incoming.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def close(self) -> None:
        self.incoming.put_nowait(None)


def test_massive_quote_payload_becomes_canonical_quote() -> None:
    market = MarketRef.ephemeral(venue="massive", market="equity", source_symbol="AAPL")
    event = MassiveStockNormalizers().market_domain_event(
        {"ev": "Q", "sym": "AAPL", "bp": 190.1, "bs": 100, "ap": 190.2, "as": 200, "t": 1_700_000_000_000_000_000},
        market=market,
        channel="ticker",
    )

    assert event.kind == "quote"
    assert isinstance(event.value, Quote)
    assert event.value.bid == Decimal("190.1")
    assert event.value.ask == Decimal("190.2")
    assert event.value.source == "massive"
    assert event.observed_at.tzinfo is timezone.utc


def test_strategy_market_subscription_receives_massive_aapl_quote() -> None:
    session = _Session()

    async def connector(url: str):
        assert url == "wss://socket.massiveprivateserver.site/stocks"
        return session

    connection = MassiveStockMarketStreamConnection(
        IntegrationConnectionSpec(
            connection_id="massive-stocks-stream",
            route=IntegrationRoute(provider=ProviderRef(ProviderId.MASSIVE)),
                product=ProductFamily.SPOT,
                asset_type=AssetType.EQUITY,
            access=AccessScope.PUBLIC,
            transport=TransportKind.MARKET_STREAM,
            mode=RuntimeMode.PAPER,
        ),
        api_key="test-key",
        driver=WebSocketDriver(connector=connector),
    )
    market = MarketRef.ephemeral(venue="massive", market="equity", source_symbol="AAPL")
    application = MarketApplication()
    source = build_live_market(
        source_name="massive",
        market_service=application,
        stream_connections={"massive": connection},
    )
    source.subscribe(MarketDataSubscriptionSpec(market, (Quote.select(),), identity="aapl-strategy"))

    async def receive():
        events = source.events()
        task = asyncio.create_task(events.__anext__())
        for _ in range(20):
            if len(session.messages) >= 2:
                break
            await asyncio.sleep(0)
        assert session.messages[:2] == [
            {"action": "auth", "params": "test-key"},
            {"action": "subscribe", "params": "Q.AAPL"},
        ]
        session.incoming.put_nowait(json.dumps([{"ev": "Q", "sym": "AAPL", "bp": 190, "ap": 191, "bs": 10, "as": 20, "t": 1_700_000_000_000_000_000}]))
        envelope = await task
        await events.aclose()
        return envelope

    envelope = asyncio.run(receive())
    assert envelope.payload.value.bid == Decimal("190")
    assert envelope.payload.value.ask == Decimal("191")


def test_massive_provider_connection_is_not_an_exchange() -> None:
    connection = MassiveStockMarketStreamConnection(
        IntegrationConnectionSpec(
            connection_id="massive-stocks-stream",
            route=IntegrationRoute(provider=ProviderRef(ProviderId.MASSIVE)),
                product=ProductFamily.SPOT,
                asset_type=AssetType.EQUITY,
            access=AccessScope.PUBLIC,
            transport=TransportKind.MARKET_STREAM,
            mode=RuntimeMode.PAPER,
        ),
        api_key="test-key",
    )
    assert connection.identity.participants[0].id == ProviderId.MASSIVE


def test_massive_shared_stream_keeps_subscription_when_one_consumer_leaves() -> None:
    from kairospy.infrastructure.integrations.services.gateways.massive.stream import MassiveStockMarketStream

    session = _Session()

    async def connector(url: str):
        return session

    async def exercise() -> list[dict[str, object]]:
        stream = MassiveStockMarketStream(api_key="test-key", driver=WebSocketDriver(connector=connector))
        first = await stream.subscribe("AAPL", {"Q"})
        second = await stream.subscribe("AAPL", {"Q"})
        await stream.unsubscribe(first)
        session.incoming.put_nowait(json.dumps({"ev": "Q", "sym": "AAPL", "bp": 1, "ap": 2, "t": 1_700_000_000_000_000_000}))
        event = await anext(stream.events(second))
        await stream.unsubscribe(second)
        return [message for message in session.messages if message.get("action") == "unsubscribe"]

    unsubscribes = asyncio.run(exercise())
    subscribes = [message for message in session.messages if message.get("action") == "subscribe"]
    assert subscribes == [{"action": "subscribe", "params": "Q.AAPL"}]
    assert unsubscribes == [{"action": "unsubscribe", "params": "Q.AAPL"}]


def test_massive_options_quote_uses_options_websocket_and_canonical_quote() -> None:
    session = _Session()

    async def connector(url: str):
        assert url == "wss://socket.massiveprivateserver.site/options"
        return session

    connection = MassiveOptionsMarketStreamConnection(
        IntegrationConnectionSpec(
            connection_id="massive-options-stream",
            route=IntegrationRoute(provider=ProviderRef(ProviderId.MASSIVE)),
            product=ProductFamily.OPTIONS,
            access=AccessScope.PUBLIC,
            transport=TransportKind.MARKET_STREAM,
            mode=RuntimeMode.PAPER,
        ),
        api_key="test-key",
        driver=WebSocketDriver(connector=connector),
    )
    market = MarketRef.ephemeral(
        venue="massive", market="option", source_symbol="O:SPY241220P00720000"
    )

    async def receive():
        remote = await connection.subscribe(
            MarketFeedSubscriptionRequest(
                market=market, selector=Quote.select(), identity="option-test"
            )
        )
        assert session.messages[:2] == [
            {"action": "auth", "params": "test-key"},
            {"action": "subscribe", "params": "Q.O:SPY241220P00720000"},
        ]
        events = remote.events()
        task = asyncio.create_task(events.__anext__())
        session.incoming.put_nowait(
            json.dumps(
                {
                    "ev": "Q",
                    "sym": "O:SPY241220P00720000",
                    "bp": 9.71,
                    "ap": 9.81,
                    "bs": 17,
                    "as": 24,
                    "t": 1_644_506_128_351,
                    "q": 844090872,
                }
            )
        )
        event = await task
        await remote.close()
        return event

    event = asyncio.run(receive())
    assert event.value.market_key == "massive_option_o_spy241220p00720000"
    assert event.value.bid == Decimal("9.71")
    assert event.value.ask == Decimal("9.81")
