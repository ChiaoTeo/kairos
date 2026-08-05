from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from kairospy.infrastructure.integrations.application.connections import IntegrationConnection, IntegrationConnectionSpec
from kairospy.infrastructure.integrations.domain import (
    AssetType,
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
from kairospy.infrastructure.integrations.services.gateways.binance.options.private_rest import BinanceOptionsAccountConnection, BinanceOptionsExecutionConnection
from kairospy.infrastructure.integrations.services.gateways.binance.options.public_rest import BinanceOptionsPublicRestGateway, BinanceOptionsPublicStreamGateway
from kairospy.infrastructure.integrations.services.gateways.binance.earn import BinanceSimpleEarnGateway
from kairospy.infrastructure.integrations.services.gateways.ccxt.market import CcxtMarketGateway
from kairospy.infrastructure.integrations.services.gateways.ccxt.private import CcxtAccountGateway, CcxtExecutionGateway
from kairospy.infrastructure.integrations.services.gateways.ibkr.execution import IBKRAccountGateway, IBKRAccountStreamGateway, IBKRExecutionGateway, IBKRExecutionStreamGateway
from kairospy.infrastructure.integrations.services.gateways.massive.market import MassiveOptionsGateway, MassiveReferenceGateway, MassiveStocksGateway


GatewayFactory = Callable[[IntegrationConnectionSpec], IntegrationConnection]
_Key = tuple[IntegrationRoute, ProductFamily | None, AssetType | None, IntegrationCapability | None, TransportKind | None]


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
            registry.register(route=route, product=ProductFamily.SPOT, capability=IntegrationCapability.ACCOUNT_MARKET_PROFILE_READ, transport=TransportKind.REST, factory=BinanceSpotAccountGateway().open)
            registry.register(route=route, product=ProductFamily.SPOT, capability=IntegrationCapability.ACCOUNT_STREAM, transport=TransportKind.USER_STREAM, factory=BinanceSpotAccountStreamGateway().open)
            registry.register(route=route, product=ProductFamily.SPOT, capability=IntegrationCapability.EXECUTION_STREAM, transport=TransportKind.USER_STREAM, factory=BinanceSpotExecutionStreamGateway().open)
            registry.register(route=route, product=ProductFamily.SPOT, capability=IntegrationCapability.ORDER_ENTRY, transport=TransportKind.REST, factory=BinanceSpotExecutionGateway().open)

        for route in (
            IntegrationRoute(broker=BrokerRef(BrokerId.BINANCE)),
            IntegrationRoute(exchange=ExchangeRef(ExchangeId.BINANCE), broker=BrokerRef(BrokerId.BINANCE)),
        ):
            for futures_product in (ProductFamily.USD_M_FUTURES, ProductFamily.COIN_M_FUTURES):
                registry.register(route=route, product=futures_product, capability=IntegrationCapability.ACCOUNT_READ, transport=TransportKind.REST, factory=CcxtAccountGateway().open)
                registry.register(route=route, product=futures_product, capability=IntegrationCapability.ACCOUNT_MARKET_PROFILE_READ, transport=TransportKind.REST, factory=CcxtAccountGateway().open)
                registry.register(route=route, product=futures_product, capability=IntegrationCapability.ORDER_ENTRY, transport=TransportKind.REST, factory=CcxtExecutionGateway().open)

        for broker in (BrokerId.OKX, BrokerId("hyperliquid")):
            routes = (
                IntegrationRoute(broker=BrokerRef(broker)),
                IntegrationRoute(exchange=ExchangeRef(ExchangeId(str(broker))), broker=BrokerRef(broker)),
            )
            for route in routes:
                for product in (ProductFamily.SPOT, ProductFamily.USD_M_FUTURES, ProductFamily.COIN_M_FUTURES):
                    registry.register(route=route, product=product, capability=IntegrationCapability.ACCOUNT_READ, transport=TransportKind.REST, factory=CcxtAccountGateway().open)
                    registry.register(route=route, product=product, capability=IntegrationCapability.ACCOUNT_MARKET_PROFILE_READ, transport=TransportKind.REST, factory=CcxtAccountGateway().open)
                    registry.register(route=route, product=product, capability=IntegrationCapability.ORDER_ENTRY, transport=TransportKind.REST, factory=CcxtExecutionGateway().open)

        registry.register(route=binance, product=ProductFamily.SPOT, asset_type=AssetType.EQUITY, capability=IntegrationCapability.MARKET_DATA, transport=TransportKind.REST, factory=BinanceEquityPublicRestGateway().open)
        registry.register(route=binance, product=ProductFamily.SPOT, asset_type=AssetType.EQUITY, capability=IntegrationCapability.MARKET_STREAM, transport=TransportKind.MARKET_STREAM, factory=BinanceEquityPublicStreamGateway().open)
        registry.register(route=binance, product=ProductFamily.OPTIONS, capability=IntegrationCapability.MARKET_DATA, transport=TransportKind.REST, factory=BinanceOptionsPublicRestGateway().open)
        registry.register(route=binance, product=ProductFamily.OPTIONS, capability=IntegrationCapability.MARKET_STREAM, transport=TransportKind.MARKET_STREAM, factory=BinanceOptionsPublicStreamGateway().open)
        for route in (
            IntegrationRoute(broker=BrokerRef(BrokerId.BINANCE)),
            IntegrationRoute(exchange=ExchangeRef(ExchangeId.BINANCE), broker=BrokerRef(BrokerId.BINANCE)),
        ):
            registry.register(route=route, product=ProductFamily.OPTIONS, capability=IntegrationCapability.ACCOUNT_READ, transport=TransportKind.REST, factory=BinanceOptionsAccountConnection)
            registry.register(route=route, product=ProductFamily.OPTIONS, capability=IntegrationCapability.ORDER_ENTRY, transport=TransportKind.REST, factory=BinanceOptionsExecutionConnection)
            registry.register(route=route, product=None, capability=IntegrationCapability.EARN, transport=TransportKind.REST, factory=BinanceSimpleEarnGateway().open)
        for exchange in (ExchangeId.BINANCE, ExchangeId.OKX, ExchangeId.HYPERLIQUID):
            registry.register(route=IntegrationRoute(exchange=ExchangeRef(exchange)), product=ProductFamily.USD_M_FUTURES, factory=CcxtMarketGateway().open)
        for exchange in (ExchangeId.OKX, ExchangeId.HYPERLIQUID):
            registry.register(route=IntegrationRoute(exchange=ExchangeRef(exchange)), product=ProductFamily.SPOT, factory=CcxtMarketGateway().open)
        registry.register(route=IntegrationRoute(exchange=ExchangeRef(ExchangeId.OKX)), product=ProductFamily.SPOT, asset_type=AssetType.EQUITY, factory=CcxtMarketGateway().open)
        ibkr = IntegrationRoute(broker=BrokerRef(BrokerId.IBKR))
        registry.register(route=ibkr, product=ProductFamily.SPOT, asset_type=AssetType.EQUITY, capability=IntegrationCapability.ACCOUNT_READ, transport=TransportKind.REQUEST_API, factory=IBKRAccountGateway().open)
        registry.register(route=ibkr, product=ProductFamily.SPOT, asset_type=AssetType.EQUITY, capability=IntegrationCapability.ACCOUNT_STREAM, transport=TransportKind.REQUEST_API, factory=IBKRAccountStreamGateway().open)
        registry.register(route=ibkr, product=ProductFamily.SPOT, asset_type=AssetType.EQUITY, capability=IntegrationCapability.ORDER_ENTRY, transport=TransportKind.REQUEST_API, factory=IBKRExecutionGateway().open)
        registry.register(route=ibkr, product=ProductFamily.SPOT, asset_type=AssetType.EQUITY, capability=IntegrationCapability.EXECUTION_STREAM, transport=TransportKind.REQUEST_API, factory=IBKRExecutionStreamGateway().open)
        registry.register(route=IntegrationRoute(provider=ProviderRef(ProviderId.MASSIVE), broker=BrokerRef(BrokerId.BINANCE)), product=ProductFamily.SPOT, capability=IntegrationCapability.MARKET_DATA, transport=TransportKind.REST, factory=BinanceSpotPublicRestGateway().open)
        registry.register(route=IntegrationRoute(provider=ProviderRef(ProviderId.MASSIVE), broker=BrokerRef(BrokerId.BINANCE), exchange=ExchangeRef(ExchangeId.BINANCE)), product=ProductFamily.SPOT, capability=IntegrationCapability.MARKET_DATA, transport=TransportKind.REST, factory=BinanceSpotPublicRestGateway().open)
        massive = IntegrationRoute(provider=ProviderRef(ProviderId.MASSIVE))
        registry.register(route=massive, product=ProductFamily.SPOT, asset_type=AssetType.EQUITY, factory=MassiveStocksGateway().open)
        registry.register(route=massive, product=ProductFamily.OPTIONS, factory=MassiveOptionsGateway().open)
        registry.register(route=massive, product=None, factory=MassiveReferenceGateway().open)
        return registry

    def register(
        self,
        *,
        route: IntegrationRoute,
        product: ProductFamily | None,
        factory: GatewayFactory,
        asset_type: AssetType | None = None,
        capability: IntegrationCapability | None = None,
        transport: TransportKind | None = None,
    ) -> None:
        key = (route, product, asset_type, capability, transport)
        if key in self._factories:
            raise ValueError(f"integration gateway already registered: {key!r}")
        self._factories[key] = factory

    def create(self, spec: IntegrationConnectionSpec) -> IntegrationConnection:
        exact = self._factories.get((spec.route, spec.product, spec.asset_type, spec.capability, spec.transport))
        if exact is not None:
            return exact(spec)
        candidates = [
            (len(route.participants) * 10 + int(asset_type is not None) + int(capability is not None) + int(transport is not None), factory)
            for (route, product, asset_type, capability, transport), factory in self._factories.items()
            if product in {spec.product, None}
            and asset_type in {spec.asset_type, None}
            and capability in {spec.capability, None}
            and transport in {spec.transport, None}
            and set(route.participants).issubset(set(spec.route.participants))
        ]
        if not candidates:
            raise LookupError(f"no integration gateway for route={spec.route!r}, product={spec.product!r}, capability={spec.capability!r}, transport={spec.transport!r}")
        _, factory = max(candidates, key=lambda item: item[0])
        return factory(spec)


__all__ = ["GatewayFactory", "GatewayRegistry"]
