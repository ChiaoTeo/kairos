from __future__ import annotations

from kairospy.application.runtime.protocol.lines import RuntimeEventLine
from kairospy.application.service.runtime.market import RuntimeMarketDataServiceView, StreamingMarketDataService
from kairospy.infrastructure.integrations.protocols import LiveMarketDataFeed


class PaperMarketDataService(StreamingMarketDataService):
    @classmethod
    def from_feed(cls, feed: LiveMarketDataFeed, *, source_name: str = "paper-live-feed") -> "PaperMarketDataService":
        return cls(feed=feed, source_name=source_name)

    def __init__(
        self,
        source: RuntimeEventLine | None = None,
        *,
        feed: LiveMarketDataFeed | None = None,
        source_name: str = "paper",
    ) -> None:
        super().__init__(source, feed=feed, source_name=source_name, mode_label="paper")


PaperMarketDataServiceView = RuntimeMarketDataServiceView

__all__ = ["PaperMarketDataService", "PaperMarketDataServiceView"]
