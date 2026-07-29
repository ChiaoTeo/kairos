from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class IntentStateSummary:
    intent_id: str
    instrument_id: str | None
    status: str
    active: bool
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class IntentJournalView:
    total_count: int = 0
    active_count: int = 0
    states: tuple[IntentStateSummary, ...] = ()


__all__ = ["IntentJournalView", "IntentStateSummary"]
