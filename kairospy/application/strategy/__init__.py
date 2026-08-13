"""Strategy runtime application facade.

User-authored strategy code must import its contract from ``kairospy.strategy``.
This package is for runtime composition and lifecycle control.
"""

from .application import (
    EventStream,
    LifecycleJournal,
    Strategy,
    StrategyEntrypoint,
    StrategyHost,
    StrategyHostStatus,
    StrategyProcessApplication,
    load_strategy,
)
from .domain.lifecycle import StrategyLifecycle

__all__ = [
    "EventStream",
    "LifecycleJournal",
    "Strategy",
    "StrategyEntrypoint",
    "StrategyHost",
    "StrategyHostStatus",
    "StrategyProcessApplication",
    "StrategyLifecycle",
    "load_strategy",
]
