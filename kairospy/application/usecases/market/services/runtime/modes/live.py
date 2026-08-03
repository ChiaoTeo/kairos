from __future__ import annotations

from typing import Mapping

from kairospy.infrastructure.integrations.application.market import MarketStreamConnection
from kairospy.application.support.runtime.domain.lines import RuntimeEventLine
from kairospy.application.support.runtime.domain.connections import ConnectionManager
from kairospy.application.usecases.market.services.runtime.common import RuntimeMarketDataServiceView
from kairospy.application.usecases.market.services.runtime.streaming import StreamingMarketDataService


class LiveMarketDataService(StreamingMarketDataService):
    def __init__(
        self,
        source: RuntimeEventLine | None = None,
        *,
        feed: MarketStreamConnection | None = None,
        feed_resolver: object | None = None,
        source_name: str = "live",
        connections: ConnectionManager | None = None,
        stream_connections: Mapping[str, MarketStreamConnection] | None = None,
    ) -> None:
        super().__init__(source, feed=feed, feed_resolver=feed_resolver, source_name=source_name, mode_label="live", connections=connections, stream_connections=stream_connections)


LiveMarketDataServiceView = RuntimeMarketDataServiceView


__all__ = ["LiveMarketDataService", "LiveMarketDataServiceView"]
