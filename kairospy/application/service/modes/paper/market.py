from __future__ import annotations

from kairospy.application.protocol.lines import RuntimeEventLine
from kairospy.application.runtime.services.market import MarketFeedResolver, RuntimeMarketDataServiceView, StreamingMarketDataService
from kairospy.application.ports import MarketStreamGateway
from kairospy.application.runtime.connections import ConnectionManager


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


PaperMarketDataServiceView = RuntimeMarketDataServiceView

__all__ = ["PaperMarketDataService", "PaperMarketDataServiceView"]
