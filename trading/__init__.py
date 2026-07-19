"""Multi-asset research, backtest, reconciliation, and trading toolkit."""

__version__ = "0.1.0"
from trading.api import BacktestRequest, BacktestResultView, BacktestRunner, Trader

__all__ = ["BacktestRequest", "BacktestResultView", "BacktestRunner", "Trader", "__version__"]
