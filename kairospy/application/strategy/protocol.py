from __future__ import annotations

from typing import Protocol

from kairospy.application.context import Context, StrategyContext
from kairospy.application.strategy.events import StrategySignal


StrategyOutput = object | None


class Strategy(Protocol):
    @property
    def strategy_id(self) -> str:
        ...

    def on_start(self, context: StrategyContext) -> StrategyOutput:
        ...

    def on_market(self, context: StrategyContext, signal: StrategySignal) -> StrategyOutput:
        ...

    def on_account(self, context: StrategyContext, signal: StrategySignal) -> StrategyOutput:
        ...

    def on_order(self, context: StrategyContext, signal: StrategySignal) -> StrategyOutput:
        ...

    def on_clock(self, context: StrategyContext, signal: StrategySignal) -> StrategyOutput:
        ...

    def on_system(self, context: StrategyContext, signal: StrategySignal) -> StrategyOutput:
        ...

    def on_end(self, context: StrategyContext) -> StrategyOutput:
        ...


class StrategyBase:
    strategy_id = "strategy"

    def on_start(self, context: StrategyContext) -> StrategyOutput:
        return ()

    def on_market(self, context: StrategyContext, signal: StrategySignal) -> StrategyOutput:
        return ()

    def on_account(self, context: StrategyContext, signal: StrategySignal) -> StrategyOutput:
        return ()

    def on_order(self, context: StrategyContext, signal: StrategySignal) -> StrategyOutput:
        return ()

    def on_clock(self, context: StrategyContext, signal: StrategySignal) -> StrategyOutput:
        return ()

    def on_system(self, context: StrategyContext, signal: StrategySignal) -> StrategyOutput:
        return ()

    def on_end(self, context: StrategyContext) -> StrategyOutput:
        return ()


__all__ = ["Context", "Strategy", "StrategyBase", "StrategyContext", "StrategyOutput"]
