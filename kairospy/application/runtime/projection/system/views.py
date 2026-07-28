from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class StrategyRunView:
    strategy_id: str
    event_count: int = 0
    runtime_event_count: int = 0
    last_event_time: datetime | None = None
    last_stream: str | None = None
    last_runtime_event_time: datetime | None = None
    last_runtime_stream: str | None = None
    status: str = "initialized"


@dataclass(frozen=True, slots=True)
class ControlRequestSummary:
    request_id: str
    strategy_id: str
    kind: str
    requested_at: datetime | None = None
    payload: tuple[tuple[str, object], ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ControlJournalView:
    total_count: int = 0
    requests: tuple[ControlRequestSummary, ...] = ()


__all__ = ["ControlJournalView", "ControlRequestSummary", "StrategyRunView"]
