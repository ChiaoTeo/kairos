from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from kairospy.infrastructure.integrations.application.connections import IntegrationConnection, IntegrationConnectionSpec
from kairospy.infrastructure.integrations.domain import (
    BrokerId,
    BrokerRef,
    ExchangeId,
    ExchangeRef,
    IntegrationCapability,
    IntegrationRoute,
    ProductFamily,
    ProviderId,
    ProviderRef,
    TransportKind,
)
from kairospy.infrastructure.integrations.services.gateways.binance.equity.public_rest import BinanceEquityPublicRestGateway, BinanceEquityPublicStreamGateway
from kairospy.infrastructure.integrations.services.gateways.binance.spot.private_rest import BinanceSpotAccountGateway, BinanceSpotExecutionGateway
from kairospy.infrastructure.integrations.services.gateways.binance.spot.public_rest import BinanceSpotPublicRestGateway
from kairospy.infrastructure.integrations.services.gateways.binance.spot.public_stream import BinanceSpotPublicStreamGateway
from kairospy.infrastructure.integrations.services.gateways.binance.spot.user_stream import BinanceSpotAccountStreamGateway, BinanceSpotExecutionStreamGateway
from kairospy.infrastructure.integrations.services.gateways.ccxt.market import CcxtMarketGateway
from kairospy.infrastructure.integrations.services.gateways.ibkr.execution import IBKRExecutionGateway
from kairospy.infrastructure.integrations.services.gateways.massive.market import MassiveOptionsGateway, MassiveReferenceGateway, MassiveStocksGateway


GatewayFactory = Callable[[IntegrationConnectionSpec], IntegrationConnection]
_Key = tuple[IntegrationRoute, ProductFamily | None, IntegrationCapability | None, TransportKind | None]


@dataclass(slots=True)
class GatewayRegistry:
    _factories: dict[_Key, GatewayFactory] = field(default_factory=dict)

    @classmethod
    def with_builtins(cls) -> "GatewayRegistry":
        registry = cls()
        binance = IntegrationRoute(exchange=ExchangeRef(ExchangeId.BINANCE))
        registry.register(route=binance, product=ProductFamily.SPOT, capability=IntegrationCapability.MARKET_DATA, transport=TransportKind.REST, factory=BinanceSpotPublicRestGateway().open)
        registry.register(route=binance, product=ProductFamily.SPOT, capability=IntegrationCapability.MARKET_STREAM, transport=TransportKind.MARKET_STREAM, factory=BinanceSpotPublicStreamGateway().open)

        for route in (
            IntegrationRoute(broker=BrokerRef(BrokerId.BINANCE)),
            IntegrationRoute(exchange=ExchangeRef(ExchangeId.BINANCE), broker=BrokerRef(BrokerId.BINANCE)),
        ):
            registry.register(route=route, product=ProductFamily.SPOT, capability=IntegrationCapability.ACCOUNT_READ, transport=TransportKind.REST, factory=BinanceSpotAccountGateway().open)
            registry.register(route=route, product=ProductFamily.SPOT, capability=IntegrationCapability.ACCOUNT_STREAM, transport=TransportKind.USER_STREAM, factory=BinanceSpotAccountStreamGateway().open)
            registry.register(route=route, product=ProductFamily.SPOT, capability=IntegrationCapability.EXECUTION_STREAM, transport=TransportKind.USER_STREAM, factory=BinanceSpotExecutionStreamGateway().open)
            registry.register(route=route, product=ProductFamily.SPOT, capability=IntegrationCapability.ORDER_ENTRY, transport=TransportKind.REST, factory=BinanceSpotExecutionGateway().open)

        registry.register(route=binance, product=ProductFamily.EQUITY, capability=IntegrationCapability.MARKET_DATA, transport=TransportKind.REST, factory=BinanceEquityPublicRestGateway().open)
        registry.register(route=binance, product=ProductFamily.EQUITY, capability=IntegrationCapability.MARKET_STREAM, transport=TransportKind.MARKET_STREAM, factory=BinanceEquityPublicStreamGateway().open)
        for exchange in (ExchangeId.BINANCE, ExchangeId.OKX, ExchangeId.HYPERLIQUID):
            registry.register(route=IntegrationRoute(exchange=ExchangeRef(exchange)), product=ProductFamily.USD_M_FUTURES, factory=CcxtMarketGateway().open)
        for exchange in (ExchangeId.OKX, ExchangeId.HYPERLIQUID):
            registry.register(route=IntegrationRoute(exchange=ExchangeRef(exchange)), product=ProductFamily.SPOT, factory=CcxtMarketGateway().open)
        registry.register(route=IntegrationRoute(exchange=ExchangeRef(ExchangeId.OKX)), product=ProductFamily.EQUITY, factory=CcxtMarketGateway().open)
        registry.register(route=IntegrationRoute(broker=BrokerRef(BrokerId.IBKR)), product=ProductFamily.EQUITY, factory=IBKRExecutionGateway().open)
        registry.register(route=IntegrationRoute(provider=ProviderRef(ProviderId.MASSIVE), broker=BrokerRef(BrokerId.BINANCE)), product=ProductFamily.SPOT, capability=IntegrationCapability.MARKET_DATA, transport=TransportKind.REST, factory=BinanceSpotPublicRestGateway().open)
        registry.register(route=IntegrationRoute(provider=ProviderRef(ProviderId.MASSIVE), broker=BrokerRef(BrokerId.BINANCE), exchange=ExchangeRef(ExchangeId.BINANCE)), product=ProductFamily.SPOT, capability=IntegrationCapability.MARKET_DATA, transport=TransportKind.REST, factory=BinanceSpotPublicRestGateway().open)
        massive = IntegrationRoute(provider=ProviderRef(ProviderId.MASSIVE))
        registry.register(route=massive, product=ProductFamily.EQUITY, factory=MassiveStocksGateway().open)
        registry.register(route=massive, product=ProductFamily.OPTIONS, factory=MassiveOptionsGateway().open)
        registry.register(route=massive, product=None, factory=MassiveReferenceGateway().open)
        return registry

    def register(
        self,
        *,
        route: IntegrationRoute,
        product: ProductFamily | None,
        factory: GatewayFactory,
        capability: IntegrationCapability | None = None,
        transport: TransportKind | None = None,
    ) -> None:
        key = (route, product, capability, transport)
        if key in self._factories:
            raise ValueError(f"integration gateway already registered: {key!r}")
        self._factories[key] = factory

    def create(self, spec: IntegrationConnectionSpec) -> IntegrationConnection:
        exact = self._factories.get((spec.route, spec.product, spec.capability, spec.transport))
        if exact is not None:
            return exact(spec)
        candidates = [
            (len(route.participants) * 10 + int(capability is not None) + int(transport is not None), factory)
            for (route, product, capability, transport), factory in self._factories.items()
            if product in {spec.product, None}
            and capability in {spec.capability, None}
            and transport in {spec.transport, None}
            and set(route.participants).issubset(set(spec.route.participants))
        ]
        if not candidates:
            raise LookupError(f"no integration gateway for route={spec.route!r}, product={spec.product!r}, capability={spec.capability!r}, transport={spec.transport!r}")
        _, factory = max(candidates, key=lambda item: item[0])
        return factory(spec)


__all__ = ["GatewayFactory", "GatewayRegistry"]
