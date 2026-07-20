"""Kairos quantitative study, backtest, reconciliation, and execution toolkit."""

__version__ = "0.1.0"

__all__ = [
    "BacktestRequest",
    "BacktestResultView",
    "BacktestRunner",
    "Kairos",
    "__version__",
    "initialize_project",
    "study_platform",
]


def __getattr__(name: str):
    if name in {"BacktestRequest", "BacktestResultView", "BacktestRunner", "Kairos"}:
        from kairospy.api import BacktestRequest, BacktestResultView, BacktestRunner, Kairos

        values = {
            "BacktestRequest": BacktestRequest,
            "BacktestResultView": BacktestResultView,
            "BacktestRunner": BacktestRunner,
            "Kairos": Kairos,
        }
        return values[name]
    if name == "initialize_project":
        from kairospy.project import initialize_project

        return initialize_project
    if name == "study_platform":
        from kairospy import study_platform

        return study_platform
    raise AttributeError(f"module 'kairospy' has no attribute {name!r}")
