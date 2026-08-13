"""Private Strategy services, exposed lazily to avoid facade dependency cycles."""

from __future__ import annotations

from importlib import import_module


_EXPORTS = {
    "InMemoryApplicationPorts": (".fakes", "InMemoryApplicationPorts"),
    "InMemoryMarketEventSource": (".fakes", "InMemoryMarketEventSource"),
    "InMemoryLifecycleJournal": (".fakes", "InMemoryLifecycleJournal"),
    "InMemoryMarketSnapshotReader": (".fakes", "InMemoryMarketSnapshotReader"),
    "build_in_memory_strategy_applications": (
        ".fakes",
        "build_in_memory_strategy_applications",
    ),
    "StrategyContext": (".context", "StrategyContext"),
    "StrategyControlServer": (".rest", "StrategyControlServer"),
    "StrategyEntrypoint": (".loader", "StrategyEntrypoint"),
    "StrategyLifecycleJournal": (".journal", "StrategyLifecycleJournal"),
    "load_strategy": (".loader", "load_strategy"),
}

__all__ = tuple(_EXPORTS)


def __getattr__(name: str):
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(name) from error
    value = getattr(import_module(f"{__name__}{module_name}"), attribute)
    globals()[name] = value
    return value
