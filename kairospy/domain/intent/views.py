from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.domain.views import ViewFieldSchema, ViewSchema


class IntentViewKeys:
    journal = "intent.journal"
    system_intents = "system.intents"


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


INTENT_JOURNAL_SCHEMA = ViewSchema(
    IntentViewKeys.journal,
    "intent",
    fields=(
        ViewFieldSchema("total_count", "known intent state count", "runtime state", "intent journal"),
        ViewFieldSchema("active_count", "active intent state count", "runtime state", "intent journal"),
        ViewFieldSchema("states", "intent state summaries", "runtime state", "intent journal"),
    ),
    mutability="runtime_writable",
    evidence="runtime intent journal view state",
)

SYSTEM_INTENTS_SCHEMA = ViewSchema(
    IntentViewKeys.system_intents,
    "system",
    fields=(
        ViewFieldSchema("total_count", "known strategy intent count", "runtime state", "IntentJournal"),
        ViewFieldSchema("active_count", "active strategy intent count", "runtime state", "IntentJournal"),
        ViewFieldSchema("states", "strategy intent state summaries", "runtime state", "IntentJournal"),
    ),
    mutability="runtime_writable",
    evidence="intent journal view state",
)


__all__ = ["INTENT_JOURNAL_SCHEMA", "SYSTEM_INTENTS_SCHEMA", "IntentJournalView", "IntentStateSummary", "IntentViewKeys"]
