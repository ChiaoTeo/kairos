from __future__ import annotations

from kairospy.application.support.runtime.services.market.feed import MarketStreamGateway
from kairospy.application.support.runtime.lines import RuntimeEventLine
from kairospy.application.support.runtime.connections import ConnectionManager
from kairospy.application.support.runtime.services.market.common import RuntimeMarketDataServiceView
from kairospy.application.support.runtime.services.market.streaming import MarketFeedResolver, StreamingMarketDataService


class PaperMarketDataService(StreamingMarketDataService):
    @classmethod
    def from_feed(cls, feed: MarketStreamGateway, *, source_name: str = "paper-live-feed") -> "PaperMarketDataService":
        return cls(feed=feed, source_name=source_name)

    def __init__(
        self,
        source: RuntimeEventLine | None = None,
        *,
        feed: MarketStreamGateway | None = None,
        feed_resolver: MarketFeedResolver | None = None,
        source_name: str = "paper",
        connections: ConnectionManager | None = None,
    ) -> None:
        super().__init__(source, feed=feed, feed_resolver=feed_resolver, source_name=source_name, mode_label="paper", connections=connections)


MarketDataServiceView = RuntimeMarketDataServiceView
PaperMarketDataServiceView = RuntimeMarketDataServiceView


__all__ = ["MarketDataServiceView", "PaperMarketDataService", "PaperMarketDataServiceView"]
