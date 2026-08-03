from __future__ import annotations

from kairospy.application.usecases.intent.services.runtime import IntentProcessor
from kairospy.application.usecases.intent.protocol import IntentJournalPort


class IntentApplication:
    """Public intent projection application component."""

    def processor(self, *, strategy_id: str, intents: IntentJournalPort) -> IntentProcessor:
        return IntentProcessor(strategy_id=strategy_id, intents=intents)


__all__ = ["IntentApplication"]
