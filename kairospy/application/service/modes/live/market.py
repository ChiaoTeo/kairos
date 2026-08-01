from __future__ import annotations

from kairospy.application.protocol.lines import RuntimeEventLine
from kairospy.application.runtime.services.market import MarketFeedResolver, RuntimeMarketDataServiceView, StreamingMarketDataService
from kairospy.application.ports import MarketStreamGateway
from kairospy.application.runtime.connections import ConnectionManager


class LiveMarketDataService(StreamingMarketDataService):
    def __init__(
        self,
        source: RuntimeEventLine | None = None,
        *,
        feed: MarketStreamGateway | None = None,
        feed_resolver: MarketFeedResolver | None = None,
        source_name: str = "live",
        connections: ConnectionManager | None = None,
    ) -> None:
        super().__init__(source, feed=feed, feed_resolver=feed_resolver, source_name=source_name, mode_label="live", connections=connections)


LiveMarketDataServiceView = RuntimeMarketDataServiceView

__all__ = ["LiveMarketDataService", "LiveMarketDataServiceView"]
