from __future__ import annotations

from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.domain import AccessScope, ProductFamily, ProviderId, TransportKind
from kairospy.infrastructure.integrations.services.connections.base import ConnectionService
from kairospy.infrastructure.integrations.services.connection_services.binance_spot.private_rest import BinanceSpotPrivateRestConnection
from kairospy.infrastructure.integrations.services.connection_services.binance_spot.private_stream import BinanceSpotPrivateStreamConnection
from kairospy.infrastructure.integrations.services.connection_services.binance_spot.public_rest import BinanceSpotPublicRestConnection
from kairospy.infrastructure.integrations.services.connection_services.binance_spot.public_stream import BinanceSpotPublicStreamConnection


def BinanceSpotConnectionService(spec: IntegrationConnectionSpec) -> ConnectionService:
    if spec.product is not ProductFamily.SPOT:
        raise ValueError("Binance Spot connection requires the spot product")
    if spec.access is AccessScope.PUBLIC and spec.transport is TransportKind.REST:
        return BinanceSpotPublicRestConnection(spec)
    if spec.access is AccessScope.PUBLIC and spec.transport is TransportKind.MARKET_STREAM:
        return BinanceSpotPublicStreamConnection(spec)
    if spec.access is AccessScope.PRIVATE and spec.transport is TransportKind.REST:
        return BinanceSpotPrivateRestConnection(spec)
    if spec.access is AccessScope.PRIVATE and spec.transport is TransportKind.USER_STREAM:
        return BinanceSpotPrivateStreamConnection(spec)
    raise ValueError(f"unsupported Binance Spot link: {spec.access}/{spec.transport}")


class MassiveMarketConnectionService(ConnectionService):
    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        super().__init__(spec, components=())


__all__ = ["BinanceSpotConnectionService", "MassiveMarketConnectionService"]
