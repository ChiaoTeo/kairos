from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.core.intent import IntentJournal
from kairospy.core.views import ViewStore

from ...model import RuntimeDataEnvelope
from .views import IntentJournalView, IntentStateSummary


@dataclass(frozen=True, slots=True)
class IntentJournalProjection:
    strategy_id: str
    intents: IntentJournal

    def register(self, views: ViewStore) -> None:
        return None

    def on_event(self, event: RuntimeDataEnvelope) -> None:
        return None

    def publish(
        self,
        views: ViewStore,
        *,
        as_of: datetime | None,
        event_count: int,
        runtime_event_count: int,
        last_event: RuntimeDataEnvelope | None,
        last_runtime_event: RuntimeDataEnvelope | None,
        status: str,
    ) -> datetime | None:
        views.put_runtime("system.intents", self.view(), as_of=as_of, available_time=as_of)
        return as_of

    def view(self) -> IntentJournalView:
        states = self.intents.list(strategy_id=self.strategy_id)
        summaries = tuple(
            IntentStateSummary(
                intent_id=item.intent.intent_id,
                instrument_id=item.intent.instrument_id,
                status=item.status.value,
                active=item.active,
                updated_at=item.updated_at,
            )
            for item in states
        )
        return IntentJournalView(
            total_count=len(summaries),
            active_count=sum(1 for item in summaries if item.active),
            states=summaries,
        )


__all__ = ["IntentJournalProjection"]
