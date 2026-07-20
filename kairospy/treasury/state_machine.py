from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from .transfer_contracts import TERMINAL_STATUSES, TransferOperation, TransferOperationEvent, TransferStatus


_ALLOWED: dict[TransferStatus, frozenset[TransferStatus]] = {
    TransferStatus.CREATED: frozenset({TransferStatus.VALIDATED, TransferStatus.REJECTED, TransferStatus.CANCELLED}),
    TransferStatus.VALIDATED: frozenset({TransferStatus.APPROVED, TransferStatus.REJECTED, TransferStatus.CANCELLED, TransferStatus.MANUAL_REVIEW}),
    TransferStatus.APPROVED: frozenset({TransferStatus.SUBMITTED, TransferStatus.CANCELLED, TransferStatus.EXPIRED, TransferStatus.FAILED}),
    TransferStatus.SUBMITTED: frozenset({TransferStatus.SOURCE_DEBITED, TransferStatus.PROCESSING, TransferStatus.BROADCAST, TransferStatus.FAILED, TransferStatus.MANUAL_REVIEW}),
    TransferStatus.SOURCE_DEBITED: frozenset({TransferStatus.IN_TRANSIT, TransferStatus.BROADCAST, TransferStatus.PROCESSING, TransferStatus.RETURNED, TransferStatus.MANUAL_REVIEW}),
    TransferStatus.IN_TRANSIT: frozenset({TransferStatus.DESTINATION_CREDITED, TransferStatus.CONFIRMED, TransferStatus.SETTLED, TransferStatus.RETURNED, TransferStatus.MANUAL_REVIEW}),
    TransferStatus.BROADCAST: frozenset({TransferStatus.CONFIRMING, TransferStatus.FAILED, TransferStatus.MANUAL_REVIEW}),
    TransferStatus.CONFIRMING: frozenset({TransferStatus.CONFIRMED, TransferStatus.FAILED, TransferStatus.MANUAL_REVIEW}),
    TransferStatus.CONFIRMED: frozenset({TransferStatus.DESTINATION_CREDITED, TransferStatus.COMPLETED, TransferStatus.MANUAL_REVIEW}),
    TransferStatus.PROCESSING: frozenset({TransferStatus.SETTLED, TransferStatus.RETURNED, TransferStatus.FAILED, TransferStatus.MANUAL_REVIEW}),
    TransferStatus.SETTLED: frozenset({TransferStatus.DESTINATION_CREDITED, TransferStatus.COMPLETED, TransferStatus.RETURNED}),
    TransferStatus.DESTINATION_CREDITED: frozenset({TransferStatus.COMPLETED, TransferStatus.REVERSED}),
    TransferStatus.RETURNED: frozenset({TransferStatus.REVERSED, TransferStatus.COMPLETED, TransferStatus.MANUAL_REVIEW}),
    TransferStatus.MANUAL_REVIEW: frozenset({TransferStatus.APPROVED, TransferStatus.PROCESSING, TransferStatus.IN_TRANSIT, TransferStatus.FAILED, TransferStatus.REJECTED}),
}


class TransferOperationStore:
    def __init__(self, repository=None) -> None:
        self._operations: dict[str, TransferOperation] = {}
        self._events: list[TransferOperationEvent] = []
        self._provider_event_ids: set[str] = set()
        self.repository = repository
        if repository is not None:
            operations, events = repository.load()
            self._operations = {item.transfer_id: item for item in operations}
            self._events = list(events)
            self._provider_event_ids = {item.provider_event_id for item in events if item.provider_event_id is not None}

    def create(self, operation: TransferOperation, event_id: str) -> None:
        if operation.transfer_id in self._operations:
            raise ValueError(f"duplicate transfer operation: {operation.transfer_id}")
        event = TransferOperationEvent(event_id, operation.transfer_id, None, operation.status, operation.created_at)
        if self.repository is not None:
            self.repository.append(operation, event)
        self._operations[operation.transfer_id] = operation
        self._events.append(event)

    def transition(self, transfer_id: str, status: TransferStatus, at: datetime, *, event_id: str, provider_event_id: str | None = None, detail: str | None = None, **changes) -> TransferOperation:
        current = self.get(transfer_id)
        if provider_event_id is not None and provider_event_id in self._provider_event_ids:
            return current
        if at.tzinfo is None or at < current.updated_at:
            raise ValueError("transfer transition time must be timezone-aware and monotonic")
        if current.status in TERMINAL_STATUSES:
            raise ValueError(f"terminal transfer cannot transition: {current.status}")
        if status not in _ALLOWED.get(current.status, frozenset()):
            raise ValueError(f"invalid transfer transition: {current.status} -> {status}")
        updated = current.evolve(status, at, **changes)
        event = TransferOperationEvent(event_id, transfer_id, current.status, status, at, provider_event_id, detail)
        if self.repository is not None:
            self.repository.append(updated, event)
        self._operations[transfer_id] = updated
        self._events.append(event)
        if provider_event_id is not None:
            self._provider_event_ids.add(provider_event_id)
        return updated

    def get(self, transfer_id: str) -> TransferOperation:
        try:
            return self._operations[transfer_id]
        except KeyError as error:
            raise LookupError(f"unknown transfer operation: {transfer_id}") from error

    def events(self, transfer_id: str | None = None) -> tuple[TransferOperationEvent, ...]:
        return tuple(item for item in self._events if transfer_id is None or item.transfer_id == transfer_id)
