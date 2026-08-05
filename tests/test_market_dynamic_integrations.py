from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import unittest

from kairospy.application.actor.support.services.connections import IntegrationConnectionScope
from kairospy.application.usecases.market.application.integration import MarketDataConnectionRequest, MarketFeedSubscriptionRequest, MarketStreamConnectionRequest
from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.usecases.market.application.requests import MarketDataSpec, MarketDataSubscriptionSpec
from kairospy.application.usecases.market.application.runtime import build_live_market
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec, RuntimeMode
from kairospy.infrastructure.integrations.application.market_runtime import SystemMarketIntegrationRuntime
from kairospy.infrastructure.integrations.domain import AccessScope, ExchangeId, ExchangeRef, IntegrationRoute, ProductFamily, TransportKind
from kairospy.infrastructure.integrations.services.gateways.ccxt.market import CcxtMarketConnection
from kairospy.infrastructure.integrations.services.gateways.ccxt.private import CcxtAccountConnection, CcxtExecutionConnection
from kairospy.infrastructure.integrations.domain import BrokerId, BrokerRef, IntegrationCapability
from kairospy.infrastructure.integrations.services.factories.registry import GatewayRegistry
from kairospy.domain.market import Quote
from kairospy.domain.reference import MarketRef


class _FakeCcxt:
    def fetch_ticker(self, symbol: str):
        return {
            "symbol": symbol,
            "timestamp": 1_700_000_000_000,
            "bid": "100.1",
            "ask": "100.2",
            "bidVolume": "2",
            "askVolume": "3",
        }

    def fetch_ohlcv(self, symbol: str, *, timeframe: str, since: int | None, limit: int):
        return [[1_700_000_000_000, "100", "101", "99", "100.5", "12"]]

    def fetch_trades(self, symbol: str, *, limit: int):
        return [{"id": "1", "timestamp": 1_700_000_000_000, "price": "100.2", "amount": "1", "side": "buy", "cost": "100.2"}]


class _FakeIntegrationApplication:
    def connect(self, spec: IntegrationConnectionSpec):
        return CcxtMarketConnection(spec, exchange=_FakeCcxt())


class MarketDynamicIntegrationTests(unittest.TestCase):
    def test_registry_selects_all_crypto_exchange_products(self) -> None:
        registry = GatewayRegistry.with_builtins()
        combinations = (
            (ExchangeId.BINANCE, ProductFamily.SPOT),
            (ExchangeId.BINANCE, ProductFamily.USD_M_FUTURES),
            (ExchangeId.OKX, ProductFamily.SPOT),
            (ExchangeId.OKX, ProductFamily.USD_M_FUTURES),
            (ExchangeId.HYPERLIQUID, ProductFamily.SPOT),
            (ExchangeId.HYPERLIQUID, ProductFamily.USD_M_FUTURES),
        )
        for exchange, product in combinations:
            connection = registry.create(
                IntegrationConnectionSpec(
                    f"{exchange.value}-{product.value}",
                    IntegrationRoute(exchange=ExchangeRef(exchange)),
                    product,
                    AccessScope.PUBLIC,
                    TransportKind.REST,
                    mode=RuntimeMode.PAPER,
                )
            )
            if (exchange, product) in {
                (ExchangeId.BINANCE, ProductFamily.USD_M_FUTURES),
                (ExchangeId.OKX, ProductFamily.SPOT),
                (ExchangeId.OKX, ProductFamily.USD_M_FUTURES),
                (ExchangeId.HYPERLIQUID, ProductFamily.SPOT),
                (ExchangeId.HYPERLIQUID, ProductFamily.USD_M_FUTURES),
            }:
                self.assertIsInstance(connection, CcxtMarketConnection)

    def test_registry_selects_ccxt_private_connections_for_okx_and_hyperliquid(self) -> None:
        registry = GatewayRegistry.with_builtins()
        for broker in (BrokerId.OKX, BrokerId("hyperliquid")):
            route = IntegrationRoute(broker=BrokerRef(broker))
            account = registry.create(IntegrationConnectionSpec(
                f"{broker.value}-account",
                route,
                ProductFamily.USD_M_FUTURES,
                AccessScope.PRIVATE,
                TransportKind.REST,
                capability=IntegrationCapability.ACCOUNT_READ,
                mode=RuntimeMode.PAPER,
            ))
            execution = registry.create(IntegrationConnectionSpec(
                f"{broker.value}-execution",
                route,
                ProductFamily.USD_M_FUTURES,
                AccessScope.PRIVATE,
                TransportKind.REST,
                capability=IntegrationCapability.ORDER_ENTRY,
                mode=RuntimeMode.PAPER,
            ))
            self.assertIsInstance(account, CcxtAccountConnection)
            self.assertIsInstance(execution, CcxtExecutionConnection)

    def test_market_runtime_creates_typed_connection_on_demand(self) -> None:
        segment = IntegrationConnectionScope()
        runtime = SystemMarketIntegrationRuntime(scope=segment, application=_FakeIntegrationApplication())
        market = MarketRef.ephemeral(venue="okx", market="swap", source_symbol="BTC/USDT")
        connection = runtime.create_stream(
            MarketStreamConnectionRequest(
                market=market,
            )
        )
        self.assertIs(segment.get("market.okx.swap.stream"), connection)
        self.assertEqual(connection.identity.participants[0].id, ExchangeId.OKX)
        quote = connection.latest_quote("BTC/USDT")  # type: ignore[attr-defined]
        self.assertEqual(quote.bid, Decimal("100.1"))  # type: ignore[union-attr]

        async def receive() -> object:
            remote = await connection.subscribe(  # type: ignore[attr-defined]
                MarketFeedSubscriptionRequest(market, Quote.select(), "strategy-1", {"poll_seconds": 0.001})
            )
            event = await anext(remote.events())
            await connection.unsubscribe(remote.subscription_id)  # type: ignore[attr-defined]
            return event

        event = asyncio.run(receive())
        self.assertEqual(event.kind, "quote")
        runtime.remove("market.okx.swap.stream")
        self.assertIsNone(segment.get("market.okx.swap.stream"))

    def test_market_runtime_creates_data_connection_and_canonicalizes_okex(self) -> None:
        segment = IntegrationConnectionScope()
        runtime = SystemMarketIntegrationRuntime(scope=segment, application=_FakeIntegrationApplication())
        connection = runtime.create_data(
            MarketDataConnectionRequest(
                MarketDataSpec("BTC/USDT", "quote", venue="okex", market="spot"),
            )
        )
        self.assertIs(segment.get("market.okex.spot.data"), connection)
        self.assertEqual(connection.identity.participants[0].id, ExchangeId.OKX)  # type: ignore[attr-defined]
        self.assertEqual(connection.latest_quote("BTC/USDT").ask, Decimal("100.2"))  # type: ignore[union-attr]

    def test_market_application_uses_dynamic_runtime_instead_of_precreated_feed(self) -> None:
        segment = IntegrationConnectionScope()
        runtime = SystemMarketIntegrationRuntime(scope=segment, application=_FakeIntegrationApplication())
        market = MarketRef.ephemeral(venue="binance", market="spot", source_symbol="BTC/USDT")
        application = MarketApplication()
        source = build_live_market(
            source_name="dynamic-market",
            market_service=application,
            integration_runtime=runtime,
        )
        source.subscribe(MarketDataSubscriptionSpec(market, (Quote,), params={"poll_seconds": 0.001}))

        async def receive() -> object:
            return await anext(source.events())

        event = asyncio.run(receive())
        self.assertEqual(event.kind, "quote")
        self.assertIsNotNone(segment.get("market.binance.spot.stream"))


if __name__ == "__main__":
    unittest.main()
