from __future__ import annotations

from kairospy.application.usecases.intent.services.runtime import IntentProjector
from kairospy.application.usecases.intent.protocol import IntentJournalPort


class IntentApplication:
    """Public intent projection application component."""

    def projector(self, *, strategy_id: str, intents: IntentJournalPort) -> IntentProjector:
        return IntentProjector(strategy_id=strategy_id, intents=intents)


__all__ = ["IntentApplication"]
