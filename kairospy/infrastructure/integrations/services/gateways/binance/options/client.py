from __future__ import annotations

from dataclasses import dataclass, field

from kairospy.infrastructure.integrations.services.gateways.binance.spot.client import BinanceSpotRestClient
from kairospy.infrastructure.integrations.services.gateways.binance.spot.endpoints import BinanceSpotEndpoint, BinanceSpotEndpointKind


@dataclass(slots=True)
class BinanceOptionsRestClient(BinanceSpotRestClient):
    endpoint: BinanceSpotEndpoint = field(
        default_factory=lambda: BinanceSpotEndpoint(BinanceSpotEndpointKind.PRIVATE_REST, "https://eapi.binance.com")
    )


__all__ = ["BinanceOptionsRestClient"]
