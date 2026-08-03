from __future__ import annotations

from typing import Protocol

from kairospy.domain.intent import IntentState
from kairospy.domain.reference import IntentId, StrategyId


class IntentJournalPort(Protocol):
    """Minimal journal capability consumed by the intent projection use case."""

    def list(
        self,
        *,
        strategy_id: StrategyId | str | None = None,
        instrument_id: object | None = None,
        active: bool | None = None,
    ) -> tuple[IntentState, ...]:
        ...

    def get(self, intent_id: IntentId | str) -> IntentState:
        ...


__all__ = ["IntentJournalPort"]
