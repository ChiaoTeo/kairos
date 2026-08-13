"""Private strategy runtime services selected by composition."""

from .fakes import (
    InMemoryApplicationPorts,
    InMemoryEventStream,
    InMemoryLifecycleJournal,
    InMemoryMarketSnapshotReader,
)
from .composition import StrategyProcessComposition, compose_strategy_process
from .context import StrategyClientBundle, StrategyContext
from .host import StrategyHost, StrategyHostStatus
from .journal import JsonlLifecycleJournal
from .loader import StrategyEntrypoint, load_strategy
from .rest import StrategyControlServer

__all__ = [
    "InMemoryApplicationPorts",
    "InMemoryEventStream",
    "InMemoryLifecycleJournal",
    "InMemoryMarketSnapshotReader",
    "StrategyClientBundle",
    "StrategyContext",
    "StrategyControlServer",
    "StrategyEntrypoint",
    "StrategyHost",
    "StrategyHostStatus",
    "JsonlLifecycleJournal",
    "StrategyProcessComposition",
    "compose_strategy_process",
    "load_strategy",
]
