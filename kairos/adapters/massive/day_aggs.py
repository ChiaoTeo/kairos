from __future__ import annotations

from .daily_ohlcv import OptionDailyOhlcvPipeline, SpxwDailyOhlcvPipeline

OptionDayAggPipeline = OptionDailyOhlcvPipeline
SpxwDayAggPipeline = SpxwDailyOhlcvPipeline

__all__ = [
    "OptionDailyOhlcvPipeline",
    "OptionDayAggPipeline",
    "SpxwDailyOhlcvPipeline",
    "SpxwDayAggPipeline",
]
