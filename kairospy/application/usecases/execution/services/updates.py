from __future__ import annotations

from datetime import datetime, timezone
import logging

from kairospy.domain.execution import ExecutionUpdate
from kairospy.domain.intent import IntentEvent, IntentEventKind, IntentJournal, IntentState, IntentStatus
from kairospy.domain.order import OrderState, OrderStatus
from kairospy.application.usecases.execution.services.coordinator import ExecutionCoordinator
from kairospy.application.usecases.execution.protocol import OrderAuditStore


_LOGGER = logging.getLogger("kairospy.execution")


class ExecutionUpdateService:
    def __init__(self, coordinator: ExecutionCoordinator, *, intents: IntentJournal | None = None, audit_store: OrderAuditStore | None = None, instance_id: str = "local") -> None:
        self.coordinator = coordinator
        self.intents = intents
        self._seen_updates: set[tuple[object, ...]] = set()
        self.audit_store = audit_store
        self.instance_id = instance_id

    def apply(self, update: ExecutionUpdate) -> OrderState:
        key = _update_key(update)
        event_key = _event_key(update)
        if key in self._seen_updates:
            self._audit(update, event_key=event_key, outcome="duplicate")
            _LOGGER.info(
                "order_event outcome=duplicate instance=%s source=%s order_id=%s order_venue_id=%s event_id=%s kind=%s",
                self.instance_id, update.source, update.order_id, update.order_venue_id, event_key, update.kind.value,
            )
            return _existing_order(self.coordinator, update)
        self._audit(update, event_key=event_key, outcome="received")
        _LOGGER.info(
            "order_event outcome=received instance=%s source=%s order_id=%s order_venue_id=%s event_id=%s kind=%s",
            self.instance_id, update.source, update.order_id, update.order_venue_id, event_key, update.kind.value,
        )
        state = self.coordinator.apply_execution_update(update)
        self._seen_updates.add(key)
        self._audit(update, event_key=event_key, outcome="applied", order_id=state.order_id, after_status=state.status.value)
        _LOGGER.info(
            "order_event outcome=applied instance=%s order_id=%s order_venue_id=%s event_id=%s status=%s filled=%s remaining=%s",
            self.instance_id, state.order_id, state.order_venue_id, event_key, state.status.value, state.filled_quantity, state.remaining_quantity,
        )
        self._record_intent_order_state(state, at=update.observed_at)
        return state

    def _audit(self, update: ExecutionUpdate, *, event_key: str, outcome: str, order_id: str | None = None, after_status: str | None = None) -> None:
        if self.audit_store is None:
            return
        metadata = dict(update.metadata)
        self.audit_store.record_receipt(
            {
                "record_id": f"receipt:{self.instance_id}:{event_key}:{outcome}",
                "instance_id": self.instance_id,
                "order_id": order_id or update.order_id,
                "order_venue_id": update.order_venue_id,
                "event_id": event_key,
                "account": None if update.context is None else str(update.context.segment.account_id),
                "account_segment": None if update.context is None else update.context.segment.value,
                "broker": None if update.context is None else str(update.context.segment.broker),
                "exchange": metadata.get("exchange") or metadata.get("venue") or update.source or None,
                "product_type": None if update.context is None or update.context.segment.product_family is None else str(update.context.segment.product_family),
                "symbol": None if update.instrument_id is None else str(update.instrument_id),
                "event_kind": update.kind.value,
                "outcome": outcome,
                "after_status": after_status,
                "observed_at": update.observed_at.isoformat(),
                "received_at": datetime.now(timezone.utc).isoformat(),
                "filled_quantity": None if update.filled_quantity is None else str(update.filled_quantity),
                "fill_quantity": None if update.fill_quantity is None else str(update.fill_quantity),
                "metadata": metadata,
            }
        )

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


def _existing_order(coordinator: ExecutionCoordinator, update: ExecutionUpdate) -> OrderState:
    if update.order_id:
        return coordinator.orders.get(update.order_id)
    if update.order_venue_id:
        return coordinator.orders.get_by_order_venue_id(update.order_venue_id)
    raise ValueError("duplicate execution update has no order identity")


def _update_key(update: ExecutionUpdate) -> tuple[object, ...]:
    metadata = update.metadata
    for name in ("event_id", "execution_id", "trade_id", "fill_id"):
        value = metadata.get(name)
        if value is not None and str(value).strip():
            return ("event", name, str(value))
    return (
        "payload",
        update.observed_at,
        update.kind,
        update.order_id,
        update.order_venue_id,
        update.fill_quantity,
        update.fill_price,
        update.filled_quantity,
        update.balance_delta,
        update.fee_currency,
        update.fee_amount,
        update.reason,
        update.source,
    )


def _event_key(update: ExecutionUpdate) -> str:
    for name in ("event_id", "execution_id", "trade_id", "fill_id"):
        value = update.metadata.get(name)
        if value is not None and str(value).strip():
            return f"{name}:{value}"
    return ":".join(
        str(value or "")
        for value in (update.source, update.order_venue_id, update.order_id, update.observed_at.isoformat(), update.kind.value, update.fill_quantity, update.filled_quantity)
    )


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


__all__ = ["ExecutionUpdateService"]
