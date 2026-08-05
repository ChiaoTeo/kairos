from __future__ import annotations

from collections.abc import Iterable, Mapping

from kairospy.application.usecases.market.application.feed import MarketFeedResolver, MarketStreamConnection
from kairospy.application.usecases.market.application.integration import MarketIntegrationRuntime
from kairospy.application.usecases.market.protocol import MarketHistoricalClient
from kairospy.application.usecases.market.services.replay import HistoricalClientFactory
from kairospy.application.actor.support.connections import ConnectionManager
from kairospy.application.usecases.market.services.runtime.view import RuntimeMarketDataServiceView
from kairospy.application.usecases.market.services.runtime.stream import MarketRuntimeService
from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.usecases.market.domain.specs import MarketDataSpec


class PaperMarketDataService(MarketRuntimeService):
    @classmethod
    def from_feed(cls, feed: MarketStreamConnection, *, source_name: str = "paper-live-feed") -> "PaperMarketDataService":
        return cls(feed=feed, source_name=source_name)

    def __init__(
        self,
        source: MarketRuntimeSource | None = None,
        *,
        feed: MarketStreamConnection | None = None,
        feed_resolver: MarketFeedResolver | None = None,
        source_name: str = "paper",
        connections: ConnectionManager | None = None,
        stream_connections: Mapping[str, MarketStreamConnection] | None = None,
        market_service: MarketApplication | None = None,
        integration_runtime: MarketIntegrationRuntime | None = None,
        warmup_specs: Iterable[MarketDataSpec] = (),
        warmup_client_factory: HistoricalClientFactory | None = None,
    ) -> None:
        super().__init__(source, feed=feed, feed_resolver=feed_resolver, source_name=source_name, mode_label="paper", connections=connections, stream_connections=stream_connections, market_service=market_service, integration_runtime=integration_runtime, warmup_specs=warmup_specs, warmup_client_factory=warmup_client_factory)


MarketDataServiceView = RuntimeMarketDataServiceView
PaperMarketDataServiceView = RuntimeMarketDataServiceView


__all__ = ["MarketDataServiceView", "PaperMarketDataService", "PaperMarketDataServiceView"]
