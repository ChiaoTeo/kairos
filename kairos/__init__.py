"""Kairos quantitative research, backtest, reconciliation, and execution toolkit."""

__version__ = "0.1.0"

__all__ = ["BacktestRequest", "BacktestResultView", "BacktestRunner", "Kairos", "__version__"]


def __getattr__(name: str):
    if name in {"BacktestRequest", "BacktestResultView", "BacktestRunner", "Kairos"}:
        from kairos.api import BacktestRequest, BacktestResultView, BacktestRunner, Kairos

        values = {
            "BacktestRequest": BacktestRequest,
            "BacktestResultView": BacktestResultView,
            "BacktestRunner": BacktestRunner,
            "Kairos": Kairos,
        }
        return values[name]
    raise AttributeError(f"module 'kairos' has no attribute {name!r}")
