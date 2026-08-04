from __future__ import annotations

from kairospy.application.usecases.market.services.runtime.replay import ReplayMarketDataRuntimeService


class BacktestMarketDataService(ReplayMarketDataRuntimeService):
    is_finite = True


__all__ = ["BacktestMarketDataService"]
