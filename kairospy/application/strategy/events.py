from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Signal(Protocol):
    domain: str
    kind: str
    time: datetime
    sequence: int

    def changed(self, domain: str, kind: str | None = None) -> bool:
        ...

StrategySignal = Signal
MarketSignal = Signal
AccountSignal = Signal
OrderSignal = Signal
ClockSignal = Signal
SystemSignal = Signal

__all__ = [
    "AccountSignal",
    "ClockSignal",
    "MarketSignal",
    "OrderSignal",
    "Signal",
    "StrategySignal",
    "SystemSignal",
]
