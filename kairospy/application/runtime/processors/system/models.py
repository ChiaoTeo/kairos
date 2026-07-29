from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class StrategyRunView:
    strategy_id: str
    event_count: int = 0
    runtime_event_count: int = 0
    last_event_time: datetime | None = None
    last_domain: str | None = None
    last_kind: str | None = None
    status: str = "initialized"


__all__ = ["StrategyRunView"]
