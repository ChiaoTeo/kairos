"""Business-owned market runtime adapters.

These services implement market usecase behavior for live, paper, and
historical sources.  The generic runtime only consumes their event source
contract; it does not own market semantics.
"""

from .backtest import BacktestMarketDataService
from .live import LiveMarketDataService
from .paper import PaperMarketDataService
from .replay import ReplayMarketDataRuntimeService, RuntimeIterableMarketEventSource
from .stream import MarketRuntimeService
from .view import RuntimeMarketDataServiceView

__all__ = [
    "BacktestMarketDataService",
    "LiveMarketDataService",
    "MarketRuntimeService",
    "PaperMarketDataService",
    "ReplayMarketDataRuntimeService",
    "RuntimeIterableMarketEventSource",
    "RuntimeMarketDataServiceView",
]
