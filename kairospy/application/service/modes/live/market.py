from __future__ import annotations

from kairospy.application.runtime.protocol.lines import RuntimeEventLine
from kairospy.application.service.runtime.market import RuntimeMarketDataServiceView, StreamingMarketDataService
from kairospy.infrastructure.integrations.protocols import LiveMarketDataFeed


class LiveMarketDataService(StreamingMarketDataService):
    def __init__(
        self,
        source: RuntimeEventLine | None = None,
        *,
        feed: LiveMarketDataFeed | None = None,
        source_name: str = "live",
    ) -> None:
        super().__init__(source, feed=feed, source_name=source_name, mode_label="live")


LiveMarketDataServiceView = RuntimeMarketDataServiceView

__all__ = ["LiveMarketDataService", "LiveMarketDataServiceView"]
