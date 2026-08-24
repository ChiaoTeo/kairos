"""Stable public API for strategy authors."""

from . import api as _api
from .api import (
    AccountExecution,
    AccountSegmentSnapshot,
    AccountSnapshot,
    Balance,
    Bar,
    BarEvent,
    MarketEvent,
    Quote,
    QuoteEvent,
    SPOT,
    Strategy,
    StrategyContext,
)

__all__ = _api.__all__

for _name in __all__:
    if _name not in _api._DECISION_EXPORTS:
        globals()[_name] = getattr(_api, _name)


def __getattr__(name: str):
    return getattr(_api, name)
