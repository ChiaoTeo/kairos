from __future__ import annotations

from typing import Mapping

from kairospy.infrastructure.integrations.application.market import MarketStreamConnection
from kairospy.application.support.runtime.domain.lines import RuntimeEventLine
from kairospy.application.support.runtime.domain.connections import ConnectionManager
from kairospy.application.usecases.market.services.runtime.common import RuntimeMarketDataServiceView
from kairospy.application.usecases.market.services.runtime.streaming import StreamingMarketDataService


class PaperMarketDataService(StreamingMarketDataService):
    @classmethod
    def from_feed(cls, feed: MarketStreamConnection, *, source_name: str = "paper-live-feed") -> "PaperMarketDataService":
        return cls(feed=feed, source_name=source_name)

    def __init__(
        self,
        source: RuntimeEventLine | None = None,
        *,
        feed: MarketStreamConnection | None = None,
        feed_resolver: object | None = None,
        source_name: str = "paper",
        connections: ConnectionManager | None = None,
        stream_connections: Mapping[str, MarketStreamConnection] | None = None,
    ) -> None:
        super().__init__(source, feed=feed, feed_resolver=feed_resolver, source_name=source_name, mode_label="paper", connections=connections, stream_connections=stream_connections)


MarketDataServiceView = RuntimeMarketDataServiceView
PaperMarketDataServiceView = RuntimeMarketDataServiceView


__all__ = ["MarketDataServiceView", "PaperMarketDataService", "PaperMarketDataServiceView"]
