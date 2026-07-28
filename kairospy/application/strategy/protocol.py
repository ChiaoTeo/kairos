from __future__ import annotations

from typing import Protocol

from kairospy.core.intent import Intent

from .context import Context
from .events import Signal


class Strategy(Protocol):
    strategy_id: str

    def on_start(self, context: Context) -> None:
        ...

    def on_data(self, context: Context, signal: Signal) -> None:
        ...

    def on_intent(self, context: Context, intent: Intent) -> None:
        ...

    def on_clock(self, context: Context, signal: Signal) -> None:
        ...

    def on_system(self, context: Context, signal: Signal) -> None:
        ...

    def on_end(self, context: Context) -> None:
        ...


class StrategyBase:
    strategy_id = "strategy"

    def on_start(self, context: Context) -> None:
        return None

    def on_data(self, context: Context, signal: Signal) -> None:
        return None

    def on_intent(self, context: Context, intent: Intent) -> None:
        return None

    def on_clock(self, context: Context, signal: Signal) -> None:
        return None

    def on_system(self, context: Context, signal: Signal) -> None:
        return None

    def on_end(self, context: Context) -> None:
        return None


__all__ = ["Strategy", "StrategyBase"]
