"""Compatibility exports for the renamed SMA crossover research backtest module."""

from .sma_cross_research_backtest import (
    BarSeries,
    SmaCrossConfig,
    SmaCrossResult,
    SmaEquityPoint,
    SmaTrade,
    backtest_sma_cross,
    backtest_sma_cross_events,
)

__all__ = [
    "BarSeries",
    "SmaCrossConfig",
    "SmaCrossResult",
    "SmaEquityPoint",
    "SmaTrade",
    "backtest_sma_cross",
    "backtest_sma_cross_events",
]
