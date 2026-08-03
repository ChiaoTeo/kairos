from __future__ import annotations

from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.domain.intent import IntentJournalView, IntentStateSummary, IntentViewKeys, SYSTEM_INTENTS_SCHEMA
from kairospy.application.usecases.intent.protocol import IntentJournalPort


class IntentJournalViewState:
    key = IntentViewKeys.system_intents
    schema = SYSTEM_INTENTS_SCHEMA

    def __init__(self, *, strategy_id: str, intents: IntentJournalPort) -> None:
        self.strategy_id = strategy_id
        self.intents = intents

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def view(self) -> IntentJournalView:
        summaries = tuple(
            IntentStateSummary(
                intent_id=str(item.intent.intent_id),
                instrument_id=None if getattr(item.intent, "instrument_id", None) is None else str(getattr(item.intent, "instrument_id")),
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


__all__ = ["IntentJournalViewState"]
