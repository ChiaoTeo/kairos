from __future__ import annotations

from kairospy.application.service.domain.market import HistoricalMarketDataClient
from kairospy.application.service.runtime.market import ReplayMarketDataService, RuntimeMarketDataServiceView


class BacktestMarketDataService(ReplayMarketDataService):
    pass


MarketDataServiceView = RuntimeMarketDataServiceView

__all__ = [
    "HistoricalMarketDataClient",
    "BacktestMarketDataService",
    "MarketDataServiceView",
]
