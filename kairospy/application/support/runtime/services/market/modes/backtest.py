from __future__ import annotations

from kairospy.application.usecases.market.history import HistoricalMarketDataPort
from kairospy.application.support.runtime.services.market.replay import ReplayMarketDataService


class BacktestMarketDataService(ReplayMarketDataService):
    pass


__all__ = ["BacktestMarketDataService", "HistoricalMarketDataPort"]
