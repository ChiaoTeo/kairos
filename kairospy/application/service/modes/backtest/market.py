from __future__ import annotations

from kairospy.application.ports import HistoricalMarketDataPort
from kairospy.application.runtime.services.market import ReplayMarketDataService, RuntimeMarketDataServiceView


class BacktestMarketDataService(ReplayMarketDataService):
    pass


MarketDataServiceView = RuntimeMarketDataServiceView

__all__ = [
    "HistoricalMarketDataPort",
    "BacktestMarketDataService",
    "MarketDataServiceView",
]
