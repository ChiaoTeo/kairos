from __future__ import annotations

from dataclasses import is_dataclass, replace
from datetime import datetime
from datetime import timezone
from typing import Iterable

from kairospy.core.reference import InstrumentId, IntentId, StrategyId

from .model import Intent, IntentEvent, IntentEventKind, IntentState


class IntentJournal:
    def __init__(self) -> None:
        self._states: dict[str, IntentState] = {}
        self._events: list[IntentEvent] = []

    def record_intent(self, intent: Intent, *, at: datetime) -> IntentState:
        intent_id = str(intent.intent_id)
        if intent_id in self._states:
            raise ValueError(f"intent already exists: {intent.intent_id}")
        recorded = intent
        if intent.created_at is None and is_dataclass(intent):
            recorded = replace(intent, created_at=at)
        state = IntentState(recorded)
        self._states[intent_id] = state
        self._events.append(IntentEvent(intent.intent_id, IntentEventKind.CREATED, at, reason=getattr(intent, "reason", "")))
        return state

    def record(self, event: IntentEvent) -> IntentState:
        state = self.get(event.intent_id).apply(event)
        self._states[str(event.intent_id)] = state
        self._events.append(event)
        return state

    def get(self, intent_id: IntentId | str) -> IntentState:
        try:
            return self._states[str(intent_id)]
        except KeyError as error:
            raise KeyError(f"unknown intent: {intent_id}") from error

    def list(
        self,
        *,
        strategy_id: StrategyId | str | None = None,
        instrument_id: InstrumentId | str | None = None,
        active: bool | None = None,
    ) -> tuple[IntentState, ...]:
        states: Iterable[IntentState] = self._states.values()
        if strategy_id is not None:
            strategy_key = str(strategy_id)
            states = (state for state in states if str(state.intent.strategy_id) == strategy_key)
        if instrument_id is not None:
            instrument_key = str(instrument_id)
            states = (state for state in states if str(getattr(state.intent, "instrument_id", "")) == instrument_key)
        if active is not None:
            states = (state for state in states if state.active is active)
        return tuple(sorted(states, key=lambda state: state.intent.created_at or datetime.min.replace(tzinfo=timezone.utc)))

    def latest(
        self,
        *,
        strategy_id: StrategyId | str | None = None,
        instrument_id: InstrumentId | str | None = None,
        active: bool | None = None,
    ) -> IntentState | None:
        states = self.list(strategy_id=strategy_id, instrument_id=instrument_id, active=active)
        return states[-1] if states else None

    def events(self) -> tuple[IntentEvent, ...]:
        return tuple(self._events)


__all__ = ["IntentJournal"]
