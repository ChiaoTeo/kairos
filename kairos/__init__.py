"""Kairos quantitative study, backtest, reconciliation, and execution toolkit."""

__version__ = "0.1.0"

__all__ = [
    "BacktestRequest",
    "BacktestResultView",
    "BacktestRunner",
    "Kairos",
    "__version__",
    "initialize_project",
]


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
    if name == "initialize_project":
        from kairos.project import initialize_project

        return initialize_project
    raise AttributeError(f"module 'kairos' has no attribute {name!r}")
