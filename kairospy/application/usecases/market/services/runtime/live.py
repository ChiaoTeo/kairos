from __future__ import annotations

from typing import Iterable, Mapping

from kairospy.application.usecases.market.application.feed import MarketStreamConnection
from kairospy.application.actor.support.connections import ConnectionManager
from kairospy.application.usecases.market.services.runtime.view import RuntimeMarketDataServiceView
from kairospy.application.usecases.market.services.runtime.stream import MarketRuntimeService
from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.usecases.market.domain.specs import MarketDataSpec


class LiveMarketDataService(MarketRuntimeService):
    def __init__(
        self,
        source: object | None = None,
        *,
        feed: MarketStreamConnection | None = None,
        feed_resolver: object | None = None,
        source_name: str = "live",
        connections: ConnectionManager | None = None,
        stream_connections: Mapping[str, MarketStreamConnection] | None = None,
        market_service: MarketApplication | None = None,
        integration_runtime: object | None = None,
        warmup_specs: Iterable[MarketDataSpec] = (),
        warmup_client_factory: object | None = None,
    ) -> None:
        super().__init__(source, feed=feed, feed_resolver=feed_resolver, source_name=source_name, mode_label="live", connections=connections, stream_connections=stream_connections, market_service=market_service, integration_runtime=integration_runtime, warmup_specs=warmup_specs, warmup_client_factory=warmup_client_factory)


LiveMarketDataServiceView = RuntimeMarketDataServiceView


__all__ = ["LiveMarketDataService", "LiveMarketDataServiceView"]
