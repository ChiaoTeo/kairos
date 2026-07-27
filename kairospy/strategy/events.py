from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class StrategySignal:
    domain: str
    kind: str
    time: datetime
    sequence: int
    stream: str = ""
    source: str = ""
    metadata: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.domain.strip() or not self.kind.strip():
            raise ValueError("strategy signal domain and kind are required")
        if self.time.tzinfo is None:
            raise ValueError("strategy signal time must be timezone-aware")
        if self.sequence < 1:
            raise ValueError("strategy signal sequence must be positive")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def changed(self, domain: str, kind: str | None = None) -> bool:
        return self.domain == domain and (kind is None or self.kind == kind)


AccountSignal = StrategySignal
ClockSignal = StrategySignal
MarketSignal = StrategySignal
OrderSignal = StrategySignal
StrategyTrigger = StrategySignal
SystemSignal = StrategySignal


__all__ = [
    "AccountSignal",
    "ClockSignal",
    "MarketSignal",
    "OrderSignal",
    "StrategySignal",
    "StrategyTrigger",
    "SystemSignal",
]
