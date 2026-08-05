"""Business results returned by the public market application API."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kairospy.domain.market import MarketEvent

from .requests import MarketDataRow
from .resolver import ResolvedMarketData


@dataclass(frozen=True, slots=True)
class MarketDataReadResult:
    resolved: ResolvedMarketData
    rows: tuple[MarketDataRow, ...]


@dataclass(frozen=True, slots=True)
class MarketDataWriteResult:
    resolved: ResolvedMarketData
    path: Path | None = None
    count: int = 0


@dataclass(frozen=True, slots=True)
class MarketEventResult:
    event: MarketEvent | None


__all__ = ["MarketDataReadResult", "MarketDataWriteResult", "MarketEventResult"]
