"""Runtime-facing assembly entry points for the market usecase."""

from __future__ import annotations

from kairospy.application.usecases.market.services.runtime.common import RuntimeMarketDataServiceView
from kairospy.application.usecases.market.services.runtime.modes.backtest import BacktestMarketDataService
from kairospy.application.usecases.market.services.runtime.modes.live import LiveMarketDataService, LiveMarketDataServiceView
from kairospy.application.usecases.market.services.runtime.modes.paper import PaperMarketDataService, PaperMarketDataServiceView
from kairospy.application.usecases.market.services.runtime.projections import (
    RuntimeMarketProjectionService,
    RuntimeMarketService,
)
from kairospy.application.usecases.market.services.runtime.replay import RuntimeIterableMarketEventSource, ReplayMarketDataRuntimeService
from kairospy.application.usecases.market.services.runtime.streaming import StreamingMarketDataService

__all__ = [
    "BacktestMarketDataService",
    "LiveMarketDataService",
    "LiveMarketDataServiceView",
    "PaperMarketDataService",
    "PaperMarketDataServiceView",
    "ReplayMarketDataRuntimeService",
    "RuntimeIterableMarketEventSource",
    "RuntimeMarketDataServiceView",
    "RuntimeMarketProjectionService",
    "RuntimeMarketService",
    "StreamingMarketDataService",
]
