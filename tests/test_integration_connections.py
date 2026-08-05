from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import unittest

from kairospy.infrastructure.integrations.application.assembly import DefaultIntegrationConnectionApplication
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.application.connections import RuntimeMode
from kairospy.infrastructure.integrations.services.gateways.binance.spot.client import (
    BinanceRequestError,
    BinanceSpotRequestClient,
    BinanceSpotRestClient,
)
from kairospy.infrastructure.integrations.services.gateways.binance.equity.client import (
    BinanceEquityRequestError,
    BinanceEquityRestClient,
)
from kairospy.infrastructure.integrations.services.gateways.binance.equity.operations import BinanceEquityMarketOperations
from kairospy.infrastructure.integrations.services.gateways.binance.equity.normalizers import BinanceEquityNormalizers
from kairospy.infrastructure.integrations.services.gateways.binance.equity.public_stream import BinanceEquityPollingConnection
from kairospy.application.usecases.market.application.requests import MarketDataSubscriptionSpec
from kairospy.application.usecases.market.application.runtime import build_live_market
from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.system.application.business import SystemBusinessRuntime
from kairospy.application.usecases.market.application.integration import MarketFeedSubscriptionRequest
from kairospy.application.support.composition.application.integrations import connect_binance_equity
from kairospy.application.usecases.strategy.application.runtime import build_strategy_runtime_session
from kairospy.application.actor.market.application import MarketActor
from kairospy.application.actor.account.application import AccountActor
from kairospy.application.actor.monitor.application.output import MonitorOutputCoordinator
from kairospy.application.actor.support.services.connections import DefaultConnectionManager
from kairospy.infrastructure.integrations.services.drivers.websocket import WebSocketDriver
from kairospy.infrastructure.integrations.services.gateways.binance.spot.stream import BinanceSpotMarketStream
from kairospy.infrastructure.integrations.services.gateways.binance.spot.public_stream import BinanceSpotPublicStreamConnection
from kairospy.infrastructure.integrations.services.gateways.binance.spot.normalizers import BinanceSpotNormalizers
from kairospy.infrastructure.integrations.services.connections.connection import Connection
from kairospy.infrastructure.integrations.domain import ConnectionLifecycle
from kairospy.domain.account import AccountModel, AccountSegment, AccountRuntimeContext, Environment, ProductFamily
from kairospy.domain.market import Quote, TradePrint
from kairospy.domain.reference import MarketRef
from kairospy.domain.intent import IntentJournal
from kairospy.infrastructure.integrations.domain import (
    AccessScope,
    AssetType,
    BrokerId,
    BrokerRef,
    CredentialRef,
    ExchangeId,
    ExchangeRef,
    IntegrationCapability,
    IntegrationRoute,
    ParticipantKind,
    ParticipantRef,
    ProductFamily,
    ProviderId,
    ProviderRef,
    TransportKind,
)


def _business_runtime(intents: IntentJournal) -> SystemBusinessRuntime:
    return SystemBusinessRuntime(
        output=MonitorOutputCoordinator(actors=(), monitor_output=object()),
        account_actor=AccountActor(None, None, intents=intents),
    )


class IntegrationConnectionTests(unittest.TestCase):
    def test_system_connection_scope_is_the_authoritative_lifecycle(self) -> None:
        class Resource:
            def __init__(self) -> None:
                self.starts = 0
                self.stops = 0

            def start(self) -> None:
                self.starts += 1

            def stop(self) -> None:
                self.stops += 1

        resource = Resource()
        segment = DefaultConnectionManager()
        segment.register("market", resource, role="market_stream")

        segment.start()
        segment.start()
        assert resource.starts == 1
        assert segment.health()["connections"] == 1

        segment.stop()
        assert resource.stops == 1
        assert segment.health()["status"] == "stopped"

    def test_binance_equity_polling_connection_emits_quote_to_market_runtime(self) -> None:
        class Response:
            status_code = 200
            content = b'{"symbol":"AAPL","bidPrice":"180.50","askPrice":"180.52","bidSize":100,"askSize":200}'
            text = content.decode()

            @staticmethod
            def json() -> object:
                return {"symbol": "AAPL", "bidPrice": "180.50", "askPrice": "180.52", "bidSize": 100, "askSize": 200}

        class Driver:
            def request(self, method: str, url: str, *, params: dict[str, object], headers: dict[str, str]) -> Response:
                return Response()

        async def scenario() -> object:
            spec = IntegrationConnectionSpec(
                connection_id="binance-equity-test",
                route=IntegrationRoute(exchange=ExchangeRef(ExchangeId.BINANCE)),
                product=ProductFamily.SPOT,
                asset_type=AssetType.EQUITY,
                access=AccessScope.PUBLIC,
                transport=TransportKind.MARKET_STREAM,
                mode=RuntimeMode.LIVE,
            )
            client = BinanceEquityRestClient(driver=Driver())  # type: ignore[arg-type]
            client.api_key = "key"
            connection = BinanceEquityPollingConnection(spec, client=client)
            market = MarketRef.ephemeral(venue="binance", market="equity", source_symbol="AAPL")
            request = MarketFeedSubscriptionRequest(market, Quote.select(), "test", {"poll_seconds": 0.001})
            remote = await connection.subscribe(request)
            event = await anext(remote.events())
            await connection.unsubscribe(remote.subscription_id)
            return event

        event = asyncio.run(scenario())
        self.assertIsInstance(event.value, Quote)
        self.assertEqual(str(event.value.ask), "180.52")
        self.assertEqual(event.value.market_id, event.subject.subject_id)

    def test_binance_equity_composition_selects_polling_market_connection(self) -> None:
        connection = connect_binance_equity("test.binance.equity")
        self.assertIsInstance(connection, BinanceEquityPollingConnection)

    def test_binance_equity_market_runtime_delivers_quote_message(self) -> None:
        class Response:
            status_code = 200
            content = b'{"symbol":"AAPL","bidPrice":"180.50","askPrice":"180.52","bidSize":100,"askSize":200}'
            text = content.decode()

            @staticmethod
            def json() -> object:
                return {"symbol": "AAPL", "bidPrice": "180.50", "askPrice": "180.52", "bidSize": 100, "askSize": 200}

        class Driver:
            def request(self, method: str, url: str, *, params: dict[str, object], headers: dict[str, str]) -> Response:
                return Response()

        async def scenario() -> object:
            spec = IntegrationConnectionSpec(
                connection_id="binance-equity-runtime-test",
                route=IntegrationRoute(exchange=ExchangeRef(ExchangeId.BINANCE)),
                product=ProductFamily.SPOT,
                asset_type=AssetType.EQUITY,
                access=AccessScope.PUBLIC,
                transport=TransportKind.MARKET_STREAM,
                mode=RuntimeMode.LIVE,
            )
            client = BinanceEquityRestClient(driver=Driver())  # type: ignore[arg-type]
            client.api_key = "key"
            connection = BinanceEquityPollingConnection(spec, client=client)
            market = MarketRef.ephemeral(venue="binance", market="equity", source_symbol="AAPL")
            market_service = MarketApplication()
            data = build_live_market(feed=connection, source_name="binance-equity", market_service=market_service)
            data.subscribe(MarketDataSubscriptionSpec(market, (Quote,), params={"poll_seconds": 0.001}))
            envelope = await anext(data.events())
            return envelope

        envelope = asyncio.run(scenario())
        self.assertEqual(envelope.domain, "market")
        self.assertEqual(envelope.kind, "quote")
        self.assertIsInstance(envelope.payload.value, Quote)
        self.assertEqual(str(envelope.payload.value.bid), "180.50")

    def test_strategy_receives_binance_equity_quote_after_context_subscription(self) -> None:
        class Response:
            status_code = 200
            content = b'{"symbol":"AAPL","bidPrice":"180.50","askPrice":"180.52","bidSize":100,"askSize":200}'
            text = content.decode()

            @staticmethod
            def json() -> object:
                return {"symbol": "AAPL", "bidPrice": "180.50", "askPrice": "180.52", "bidSize": 100, "askSize": 200}

        class Driver:
            def request(self, method: str, url: str, *, params: dict[str, object], headers: dict[str, str]) -> Response:
                return Response()

        class Stop:
            stopped = False

            def should_stop(self) -> bool:
                return self.stopped

        class Strategy:
            strategy_id = "binance-equity-strategy-test"

            def __init__(self, market: MarketRef, stop: Stop) -> None:
                self.market = market
                self.stop = stop
                self.received: Quote | None = None

            def on_start(self, context) -> None:
                context.subscribe(self.market, selectors=(Quote,), identity=self.strategy_id)

            def on_data(self, context, signal) -> None:
                self.received = signal.payload.value
                self.stop.stopped = True

            def on_intent(self, context, intent) -> None:
                return None

            def on_clock(self, context, signal) -> None:
                return None

            def on_system(self, context, signal) -> None:
                return None

            def on_end(self, context) -> None:
                return None

        async def scenario() -> Strategy:
            spec = IntegrationConnectionSpec(
                connection_id="binance-equity-strategy-test",
                route=IntegrationRoute(exchange=ExchangeRef(ExchangeId.BINANCE)),
                product=ProductFamily.SPOT,
                asset_type=AssetType.EQUITY,
                access=AccessScope.PUBLIC,
                transport=TransportKind.MARKET_STREAM,
                mode=RuntimeMode.LIVE,
            )
            client = BinanceEquityRestClient(driver=Driver())  # type: ignore[arg-type]
            client.api_key = "key"
            connection = BinanceEquityPollingConnection(spec, client=client)
            market = MarketRef.ephemeral(venue="binance", market="equity", source_symbol="AAPL")
            market_service = MarketApplication()
            data = build_live_market(feed=connection, source_name="binance-equity", market_service=market_service)
            data.subscribe(MarketDataSubscriptionSpec(market, (Quote,), params={"poll_seconds": 0.001}))
            stop = Stop()
            data.set_stop_signal(stop)
            strategy = Strategy(market, stop)
            interaction = MarketActor(None, None, market_service=market_service)
            intents = IntentJournal()
            runtime = build_strategy_runtime_session(strategy, system_call=interaction)
            callback = _business_runtime(intents)
            events = data.events()
            async for event in events:
                instruction = callback.on_message(event)
                if instruction.dispatch_strategy:
                    cycle = runtime.process(event, hook=instruction.strategy_hook)
                    output = cycle.output
                    callback.on_strategy_result(
                        event,
                        hook=instruction.strategy_hook or "",
                        intents=tuple(getattr(output, "intents", ())),
                        context=getattr(output, "context", None),
                    )
            await data.close() if hasattr(data, "close") else None
            return strategy

        strategy = asyncio.run(scenario())
        self.assertIsNotNone(strategy.received)
        self.assertEqual(str(strategy.received.ask), "180.52")

    def test_binance_equity_latest_quote_uses_api_key_and_uppercases_symbol(self) -> None:
        class Response:
            status_code = 200
            content = b'{"symbol":"AAPL","bidPrice":"180.50","askPrice":"180.52","bidSize":100,"askSize":200}'
            text = content.decode()

            @staticmethod
            def json() -> object:
                return {"symbol": "AAPL", "bidPrice": "180.50", "askPrice": "180.52", "bidSize": 100, "askSize": 200}

        class Driver:
            def __init__(self) -> None:
                self.call: tuple[str, str, dict[str, object], dict[str, str]] | None = None

            def request(self, method: str, url: str, *, params: dict[str, object], headers: dict[str, str]) -> Response:
                self.call = (method, url, params, headers)
                return Response()

        driver = Driver()
        client = BinanceEquityRestClient(driver=driver)  # type: ignore[arg-type]
        client.api_key = "key"
        payload = BinanceEquityMarketOperations(client).latest_quote(symbol=" aapl ")

        self.assertEqual(payload["symbol"], "AAPL")  # type: ignore[index]
        assert driver.call is not None
        self.assertEqual(driver.call[0:2], ("GET", "https://api.binance.com/sapi/v1/equity/market/quote"))
        self.assertEqual(driver.call[2], {"symbol": "AAPL"})
        self.assertEqual(driver.call[3], {"X-MBX-APIKEY": "key"})

    def test_binance_equity_empty_quote_translates_to_none(self) -> None:
        class Response:
            status_code = 200
            content = b""
            text = ""

            @staticmethod
            def json() -> object:
                raise ValueError

        class Driver:
            def request(self, method: str, url: str, *, params: dict[str, object], headers: dict[str, str]) -> Response:
                return Response()

        client = BinanceEquityRestClient(driver=Driver())  # type: ignore[arg-type]
        client.api_key = "key"
        self.assertIsNone(BinanceEquityNormalizers().latest_quote(client.get("/sapi/v1/equity/market/quote")))

    def test_binance_equity_quote_translates_to_quote_model(self) -> None:
        quote = BinanceEquityNormalizers().latest_quote(
            {"symbol": "AAPL", "bidPrice": "180.50", "askPrice": "180.52", "bidSize": 100, "askSize": 200},
            observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert quote is not None
        self.assertEqual(str(quote.bid), "180.50")
        self.assertEqual(str(quote.ask), "180.52")
        self.assertEqual(str(quote.bid_size), "100")
        self.assertEqual(quote.market_key, "binance_equity_aapl")

    def test_binance_equity_requires_api_key(self) -> None:
        with self.assertRaises(BinanceEquityRequestError):
            BinanceEquityRestClient().get("/sapi/v1/equity/market/quote", params={"symbol": "AAPL"})

    def test_binance_private_rest_signs_query_inside_client(self) -> None:
        class Response:
            status_code = 200
            content = b'{"balances": []}'
            text = '{"balances": []}'

            @staticmethod
            def json() -> object:
                return {"balances": []}

        class Driver:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, dict[str, object], dict[str, str]]] = []

            def request(self, method: str, url: str, *, params: dict[str, object], headers: dict[str, str]) -> Response:
                self.calls.append((method, url, params, headers))
                return Response()

        driver = Driver()
        client = BinanceSpotRestClient(credential_id="test", driver=driver)  # type: ignore[arg-type]
        client.api_key = "key"
        client.secret = "secret"
        client.time_provider = lambda: 1700000000000

        self.assertEqual(client.get("/api/v3/account", params={"symbol": "BTCUSDT"}, signed=True), {"balances": []})
        method, url, params, headers = driver.calls[0]
        self.assertEqual((method, url), ("GET", "https://api.binance.com/api/v3/account"))
        self.assertEqual(headers["X-MBX-APIKEY"], "key")
        unsigned = {key: value for key, value in params.items() if key != "signature"}
        query = "&".join(f"{key}={value}" for key, value in unsigned.items())
        expected = hmac.new(b"secret", query.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(params["signature"], expected)

    def test_binance_rest_raises_typed_vendor_error(self) -> None:
        class Response:
            status_code = 400
            content = b'{"code": -1021, "msg": "Timestamp outside recvWindow"}'
            text = content.decode()

            @staticmethod
            def json() -> object:
                return {"code": -1021, "msg": "Timestamp outside recvWindow"}

        class Driver:
            def request(self, method: str, url: str, *, params: dict[str, object], headers: dict[str, str]) -> Response:
                return Response()

        client = BinanceSpotRestClient(driver=Driver())  # type: ignore[arg-type]
        with self.assertRaises(BinanceRequestError) as context:
            client.get("/api/v3/time")
        self.assertEqual(context.exception.status_code, 400)
        self.assertEqual(context.exception.code, -1021)

    def test_binance_request_api_uses_json_rpc_over_websocket(self) -> None:
        class Session:
            def __init__(self) -> None:
                self.sent: list[str] = []

            async def send(self, message: str) -> None:
                self.sent.append(message)

            async def recv(self) -> str:
                return '{"id": 1700000000000, "status": 200, "result": {"serverTime": 1}}'

            async def close(self) -> None:
                return None

        session = Session()

        async def connector(url: str) -> Session:
            self.assertEqual(url, "wss://ws-api.binance.com:443/ws-api/v3")
            return session

        async def scenario() -> object:
            client = BinanceSpotRequestClient(
                credential_id="test",
                driver=WebSocketDriver(connector=connector),
            )
            client.api_key = "key"
            client.secret = "secret"
            client.time_provider = lambda: 1700000000000
            return await client.request_api("time")

        self.assertEqual(asyncio.run(scenario()), {"serverTime": 1})
        self.assertIn('"method": "time"', session.sent[0])

    def test_binance_market_stream_connects_lazily_and_translates_json(self) -> None:
        class Session:
            def __init__(self) -> None:
                self.closed = False

            def __aiter__(self):
                return self

            async def __anext__(self) -> str:
                if self.closed:
                    raise StopAsyncIteration
                self.closed = True
                return '{"e": "trade", "s": "BTCUSDT"}'

            async def close(self) -> None:
                self.closed = True

        session = Session()
        urls: list[str] = []

        async def connector(url: str) -> Session:
            urls.append(url)
            return session

        async def scenario() -> dict[str, object]:
            stream = BinanceSpotMarketStream(driver=WebSocketDriver(connector=connector))
            events = [event async for event in stream.events("BTCUSDT", "trade")]
            return events[0]

        self.assertEqual(asyncio.run(scenario()), {"e": "trade", "s": "BTCUSDT"})
        self.assertEqual(urls, ["wss://stream.binance.com:9443/ws/btcusdt@trade"])

    def test_binance_public_stream_connection_subscribes_through_gateway(self) -> None:
        class Session:
            def __init__(self) -> None:
                self.closed = False

            def __aiter__(self):
                return self

            async def __anext__(self) -> str:
                if self.closed:
                    raise StopAsyncIteration
                self.closed = True
                return '{"e":"trade","s":"BTCUSDT","t":1700000000000,"p":"100.25","q":"0.5","m":false}'

            async def close(self) -> None:
                self.closed = True

        session = Session()
        urls: list[str] = []

        async def connector(url: str) -> Session:
            urls.append(url)
            return session

        async def scenario() -> object:
            spec = IntegrationConnectionSpec(
                connection_id="binance-spot-public-stream",
                route=IntegrationRoute(exchange=ExchangeRef(ExchangeId.BINANCE)),
                product=ProductFamily.SPOT,
                access=AccessScope.PUBLIC,
                transport=TransportKind.MARKET_STREAM,
                mode=RuntimeMode.PAPER,
            )
            connection = BinanceSpotPublicStreamConnection(spec)
            connection.stream.driver = WebSocketDriver(connector=connector)
            market = MarketRef.ephemeral(venue="binance", market="spot", source_symbol="BTCUSDT")
            remote = await connection.subscribe(MarketFeedSubscriptionRequest(market, TradePrint.select(), "test"))
            event = await anext(remote.events())
            await connection.unsubscribe(remote.subscription_id)
            return event

        event = asyncio.run(scenario())
        self.assertEqual(event.value.market_key, "binance_spot_btcusdt")
        self.assertEqual(urls, ["wss://stream.binance.com:9443/ws/btcusdt@trade"])

    def test_binance_public_payload_translator_returns_system_models(self) -> None:
        translator = BinanceSpotNormalizers()
        bars = tuple(
            translator.bars(
                [[1700000000000, "100", "110", "90", "105", "12"]],
                symbol="BTCUSDT",
                timeframe="1m",
            )
        )
        self.assertEqual(str(bars[0].close), "105")
        catalog = translator.catalog(
            {
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "baseAsset": "BTC",
                        "quoteAsset": "USDT",
                        "status": "TRADING",
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                            {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001"},
                            {"filterType": "MIN_NOTIONAL", "minNotional": "10"},
                        ],
                    }
                ]
            },
            as_of=datetime.now(timezone.utc),
        )
        self.assertEqual(len(catalog.list_markets(at=datetime.now(timezone.utc))), 1)
        snapshot = translator.account_snapshot(
            {"balances": [{"asset": "USDT", "free": "10.5", "locked": "1.5"}]},
            context=AccountRuntimeContext(AccountSegment("binance", "live", AccountModel.NO_MARGIN, ProductFamily.SPOT), Environment.LIVE),
            observed_at=datetime.now(timezone.utc),
        )
        self.assertEqual(snapshot.balances[0].total, 12)

    def test_binance_public_stream_payload_translates_to_market_event(self) -> None:
        translator = BinanceSpotNormalizers()
        market = MarketRef.ephemeral(
            venue="binance", market="spot", source_symbol="BTCUSDT"
        )
        event = translator.market_domain_event(
            {"e": "trade", "E": 1700000000000, "t": 7, "p": "100", "q": "0.2", "m": False},
            market=market,
            channel="trade",
        )
        self.assertEqual(event.kind, "trade")
        self.assertEqual(str(event.value.price), "100")

    def test_binance_private_order_event_translates_to_execution_update(self) -> None:
        translator = BinanceSpotNormalizers()
        context = AccountRuntimeContext(AccountSegment("binance", "live", AccountModel.NO_MARGIN, ProductFamily.SPOT), Environment.LIVE)
        update = translator.execution_update(
            {
                "E": 1700000000000,
                "i": 42,
                "s": "BTCUSDT",
                "S": "BUY",
                "o": "LIMIT",
                "q": "0.2",
                "p": "100",
                "z": "0.2",
                "l": "0.2",
                "L": "100",
                "X": "FILLED",
                "n": "0.01",
                "N": "USDT",
            },
            context=context,
        )
        self.assertEqual(update.kind.value, "filled")
        self.assertEqual(update.order_venue_id, "42")
        self.assertEqual(str(update.fill_quantity), "0.2")
        self.assertEqual(update.fee_currency, "USDT")

    def test_participant_roles_are_typed_and_distinct(self) -> None:
        exchange = ExchangeRef(ExchangeId.BINANCE).participant
        broker = BrokerRef(BrokerId.BINANCE).participant

        self.assertEqual(exchange.kind, ParticipantKind.EXCHANGE)
        self.assertEqual(broker.kind, ParticipantKind.BROKER)
        self.assertNotEqual(exchange, broker)
        with self.assertRaises(TypeError):
            ParticipantRef(ParticipantKind.EXCHANGE, BrokerId.BINANCE)  # type: ignore[arg-type]

    def test_execution_route_must_match_account_route(self) -> None:
        self.assertNotEqual(BrokerRef(BrokerId.BINANCE).participant, BrokerRef(BrokerId.OKX).participant)

    def test_binance_spot_connection_is_system_scoped(self) -> None:
        spec = IntegrationConnectionSpec(
            connection_id="binance-spot-live",
            route=IntegrationRoute(broker=BrokerRef(BrokerId.BINANCE)),
            product=ProductFamily.SPOT,
            access=AccessScope.PRIVATE,
            transport=TransportKind.REST,
            credential=CredentialRef("binance-live"),
            mode=RuntimeMode.LIVE,
        )
        connection = DefaultIntegrationConnectionApplication().connect(spec)

        self.assertEqual(connection.identity.connection_id, "binance-spot-live")
        self.assertEqual(connection.identity.product, ProductFamily.SPOT)
        self.assertEqual(connection.identity.participants, (BrokerRef(BrokerId.BINANCE).participant,))
        self.assertEqual(connection.access, AccessScope.PRIVATE)
        self.assertEqual(connection.transport, TransportKind.REST)
        self.assertTrue(hasattr(connection, "read_account"))
        self.assertFalse(hasattr(connection, "submit"))
        self.assertFalse(hasattr(connection, "cancel"))

        execution = DefaultIntegrationConnectionApplication().connect(
            IntegrationConnectionSpec(
                connection_id="binance-spot-execution-live",
                route=IntegrationRoute(broker=BrokerRef(BrokerId.BINANCE)),
                product=ProductFamily.SPOT,
                access=AccessScope.PRIVATE,
                transport=TransportKind.REST,
                capability=IntegrationCapability.ORDER_ENTRY,
                credential=CredentialRef("binance-live"),
                mode=RuntimeMode.LIVE,
            )
        )
        self.assertFalse(hasattr(execution, "read_account"))
        self.assertTrue(hasattr(execution, "submit"))
        self.assertTrue(hasattr(execution, "cancel"))

        async def scenario() -> None:
            await connection.start()
            self.assertTrue(connection.health().healthy)
            self.assertTrue(connection.health().authenticated)
            await connection.stop()

        asyncio.run(scenario())

    def test_connection_is_one_public_link(self) -> None:
        spec = IntegrationConnectionSpec(
            connection_id="binance-spot-access",
            route=IntegrationRoute(exchange=ExchangeRef(ExchangeId.BINANCE)),
            product=ProductFamily.SPOT,
            access=AccessScope.PUBLIC,
            transport=TransportKind.MARKET_STREAM,
            mode=RuntimeMode.PAPER,
        )
        connection = DefaultIntegrationConnectionApplication().connect(spec)
        self.assertEqual(connection.identity.participants, (ExchangeRef(ExchangeId.BINANCE).participant,))
        self.assertEqual(connection.transport, TransportKind.MARKET_STREAM)
        self.assertTrue(hasattr(connection, "subscribe"))

        async def scenario() -> None:
            await connection.start()
            self.assertTrue(connection.health().healthy)
            await connection.stop()

        asyncio.run(scenario())

    def test_live_private_connection_requires_credential(self) -> None:
        with self.assertRaises(ValueError):
            IntegrationConnectionSpec(
                connection_id="missing-credential",
                route=IntegrationRoute(broker=BrokerRef(BrokerId.BINANCE)),
                product=ProductFamily.SPOT,
                access=AccessScope.PRIVATE,
                transport=TransportKind.REST,
            )

    def test_connection_requires_at_least_one_typed_route(self) -> None:
        with self.assertRaises(ValueError):
            IntegrationConnectionSpec(
                connection_id="",
                route=IntegrationRoute(exchange=ExchangeRef(ExchangeId.BINANCE)),
                product=ProductFamily.SPOT,
                access=AccessScope.PUBLIC,
                transport=TransportKind.REST,
            )

    def test_connection_start_rolls_back_started_components_on_failure(self) -> None:
        class Component:
            def __init__(self, fail: bool = False) -> None:
                self.fail = fail
                self.started = False
                self.stopped = False

            async def start(self) -> None:
                if self.fail:
                    raise RuntimeError("component failed")
                self.started = True

            async def stop(self) -> None:
                self.stopped = True

            async def reconnect(self) -> None:
                return None

        first = Component()
        second = Component(fail=True)
        spec = IntegrationConnectionSpec(
            connection_id="rollback",
            route=IntegrationRoute(exchange=ExchangeRef(ExchangeId.BINANCE)),
            product=ProductFamily.SPOT,
            access=AccessScope.PUBLIC,
            transport=TransportKind.REST,
            mode=RuntimeMode.PAPER,
        )
        connection = Connection(spec, components=(first, second))
        with self.assertRaises(RuntimeError):
            asyncio.run(connection.start())
        self.assertTrue(first.started)
        self.assertTrue(first.stopped)
        self.assertEqual(connection.state.lifecycle, ConnectionLifecycle.FAILED)

    def test_binding_transport_is_typed(self) -> None:
        spec = IntegrationConnectionSpec(
            connection_id="binance-spot-paper",
            route=IntegrationRoute(exchange=ExchangeRef(ExchangeId.BINANCE)),
            product=ProductFamily.SPOT,
            access=AccessScope.PUBLIC,
            transport=TransportKind.REST,
            mode=RuntimeMode.PAPER,
        )
        self.assertEqual(spec.binding.access, AccessScope.PUBLIC)
        self.assertEqual(spec.binding.transport, TransportKind.REST)

    def test_binding_rejects_transport_outside_participant_access_boundary(self) -> None:
        with self.assertRaises(ValueError):
            from kairospy.infrastructure.integrations.domain import IntegrationBinding

            IntegrationBinding(
                participant=ExchangeRef(ExchangeId.BINANCE).participant,
                product=ProductFamily.SPOT,
                access=AccessScope.PUBLIC,
                transport=TransportKind.REQUEST_API,
            )

    def test_provider_connection_is_public_only(self) -> None:
        spec = IntegrationConnectionSpec(
            connection_id="massive-market",
            route=IntegrationRoute(provider=ProviderRef(ProviderId.MASSIVE)),
            product=None,
            access=AccessScope.PUBLIC,
            transport=TransportKind.REST,
        )
        connection = DefaultIntegrationConnectionApplication().connect(spec)

        self.assertEqual(connection.identity.participants[0].kind, ParticipantKind.PROVIDER)
        self.assertEqual(connection.access, AccessScope.PUBLIC)
        self.assertEqual(connection.transport, TransportKind.REST)

    def test_provider_and_private_account_are_two_connections(self) -> None:
        public = DefaultIntegrationConnectionApplication().connect(
            IntegrationConnectionSpec(
                connection_id="massive-market",
                route=IntegrationRoute(provider=ProviderRef(ProviderId.MASSIVE)),
                product=None,
                access=AccessScope.PUBLIC,
                transport=TransportKind.REST,
                mode=RuntimeMode.PAPER,
            )
        )
        private = DefaultIntegrationConnectionApplication().connect(
            IntegrationConnectionSpec(
                connection_id="binance-private",
                route=IntegrationRoute(broker=BrokerRef(BrokerId.BINANCE)),
                product=ProductFamily.SPOT,
                access=AccessScope.PRIVATE,
                transport=TransportKind.REST,
                credential=CredentialRef("binance-live"),
            )
        )
        self.assertNotEqual(public.identity.connection_id, private.identity.connection_id)
        self.assertEqual(public.identity.participants[0].kind, ParticipantKind.PROVIDER)
        self.assertEqual(private.identity.participants[0].kind, ParticipantKind.BROKER)

    def test_exchange_only_connection_does_not_create_private_account_access(self) -> None:
        spec = IntegrationConnectionSpec(
            connection_id="binance-public-market",
            route=IntegrationRoute(exchange=ExchangeRef(ExchangeId.BINANCE)),
            product=ProductFamily.SPOT,
            access=AccessScope.PUBLIC,
            transport=TransportKind.REST,
            mode=RuntimeMode.PAPER,
        )
        connection = DefaultIntegrationConnectionApplication().connect(spec)

        self.assertEqual(connection.access, AccessScope.PUBLIC)
        self.assertFalse(connection.state.bindings[0].access is AccessScope.PRIVATE)
