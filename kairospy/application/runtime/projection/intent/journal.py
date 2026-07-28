from __future__ import annotations

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.intent import IntentJournal
from kairospy.core.views import ViewFieldSchema, ViewSchema

from .views import IntentJournalView, IntentStateSummary


class IntentJournalProjection:
    key = "system.intents"
    schema = ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("total_count", "known strategy intent count", "runtime state", "IntentJournal"),
            ViewFieldSchema("active_count", "active strategy intent count", "runtime state", "IntentJournal"),
            ViewFieldSchema("states", "strategy intent state summaries", "runtime state", "IntentJournal"),
        ),
        mutability="runtime_writable",
        evidence="intent journal projection",
    )

    def __init__(self, *, strategy_id: str, intents: IntentJournal) -> None:
        self.strategy_id = strategy_id
        self.intents = intents

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def view(self) -> IntentJournalView:
        summaries = tuple(
            IntentStateSummary(
                intent_id=item.intent.intent_id,
                instrument_id=getattr(item.intent, "instrument_id", None),
                status=item.status.value,
                active=item.active,
                updated_at=item.updated_at,
            )
            for item in self.intents.list(strategy_id=self.strategy_id)
        )
        return IntentJournalView(
            total_count=len(summaries),
            active_count=sum(1 for item in summaries if item.active),
            states=summaries,
        )


__all__ = ["IntentJournalProjection"]
