from __future__ import annotations

from datetime import datetime

from kairospy.core.execution import ExecutionUpdate
from kairospy.core.intent import IntentEvent, IntentEventKind, IntentJournal, IntentState, IntentStatus
from kairospy.core.order import OrderState, OrderStatus


class ApplyExecutionUpdateUseCase:
    def __init__(self, coordinator: object, *, intents: IntentJournal | None = None) -> None:
        self.coordinator = coordinator
        self.intents = intents

    def apply(self, update: ExecutionUpdate) -> OrderState:
        state = self.coordinator.apply_execution_update(update)
        self._record_intent_order_state(state, at=update.observed_at)
        return state

    def _record_intent_order_state(self, order: OrderState, *, at: datetime) -> None:
        if self.intents is None:
            return
        intent = _intent_for_order(self.intents, order.order_id)
        if intent is None:
            return
        for kind in _intent_events_for_order_status(intent.status, order.status):
            intent = self.intents.record(
                IntentEvent(intent.intent.intent_id, kind, at, order_ids=(order.order_id,), reason=order.reason)
            )


def _intent_for_order(intents: IntentJournal, order_id: str) -> IntentState | None:
    for state in intents.list():
        if order_id in state.order_ids:
            return state
    return None


def _intent_events_for_order_status(status: IntentStatus, order_status: OrderStatus) -> tuple[IntentEventKind, ...]:
    if status.terminal:
        return ()
    if order_status is OrderStatus.ACKNOWLEDGED:
        return _path_to_ordering(status)
    if order_status is OrderStatus.PARTIALLY_FILLED:
        return (*_path_to_ordering(status), IntentEventKind.PARTIALLY_FILLED)
    if order_status is OrderStatus.FILLED:
        return (*_path_to_ordering(status), IntentEventKind.SATISFIED)
    if order_status is OrderStatus.CANCELED:
        return (IntentEventKind.CANCELED,)
    if order_status is OrderStatus.REJECTED:
        return (IntentEventKind.FAILED,) if status is IntentStatus.PARTIALLY_FILLED else (IntentEventKind.REJECTED,)
    if order_status is OrderStatus.EXPIRED:
        return (*_path_to_ordering(status), IntentEventKind.EXPIRED)
    if order_status is OrderStatus.UNKNOWN:
        return (IntentEventKind.FAILED,)
    return ()


def _path_to_ordering(status: IntentStatus) -> tuple[IntentEventKind, ...]:
    if status is IntentStatus.CREATED:
        return (IntentEventKind.ACCEPTED, IntentEventKind.PLANNED, IntentEventKind.ORDERING)
    if status is IntentStatus.ACCEPTED:
        return (IntentEventKind.PLANNED, IntentEventKind.ORDERING)
    if status is IntentStatus.PLANNED:
        return (IntentEventKind.ORDERING,)
    return ()


__all__ = ["ApplyExecutionUpdateUseCase"]
