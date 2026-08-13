"""Strategy runtime application facade.

User-authored strategy code must import its contract from ``kairospy.strategy``.
This package is for runtime composition and lifecycle control.
"""

from .application import (
    Strategy,
    StrategyEntrypoint,
    StrategyApplication,
    StrategyStatus,
    load_strategy,
)
from .domain.lifecycle import StrategyLifecycle

__all__ = [
    "Strategy",
    "StrategyEntrypoint",
    "StrategyApplication",
    "StrategyStatus",
    "StrategyLifecycle",
    "load_strategy",
]
