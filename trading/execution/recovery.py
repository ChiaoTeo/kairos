from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from trading.adapters.base import OrderRecoveryAdapter, VenueOrderStatus
from trading.domain.identity import AccountKey
from trading.execution.ingestion import DurableExecutionIngestionService
from trading.execution.order_state import DurableOrderRecord, DurableOrderStatus
from trading.orchestration.runtime_store import SQLiteRuntimeStore


@dataclass(frozen=True, slots=True)
class OrderRecoveryReport:
    resolved: tuple[str, ...]
    unresolved: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.unresolved


class VenueOrderRecoveryService:
    """Resolve crash-window orders from Venue evidence without resubmission."""

    def __init__(
        self,
        store: SQLiteRuntimeStore,
        adapters: Mapping[AccountKey, OrderRecoveryAdapter],
        ingestion: DurableExecutionIngestionService,
    ) -> None:
        self.store = store
        self.adapters = dict(adapters)
        self.ingestion = ingestion

    def recover(self, at: datetime) -> OrderRecoveryReport:
        resolved = []
        unresolved = []
        for record in self.store.orders_requiring_venue_recovery():
            if not self._recover_one(record, at):
                unresolved.append(record.request.client_order_id)
            else:
                resolved.append(record.request.client_order_id)
        return OrderRecoveryReport(tuple(resolved), tuple(unresolved))

    def _recover_one(self, record: DurableOrderRecord, at: datetime) -> bool:
        adapter = self.adapters.get(record.request.account)
        if adapter is None:
            return False
        venue_order_id = record.ack.venue_order_id if record.ack is not None else None
        outcome = adapter.recover_order(record.request.account, record.request, venue_order_id)
        if outcome.status is VenueOrderStatus.UNKNOWN:
            return False
        if not outcome.proof.strip():
            raise ValueError("venue order recovery requires auditable proof")
        current = record
        if current.status is DurableOrderStatus.SUBMITTING:
            if outcome.status is VenueOrderStatus.REJECTED:
                self.store.transition_order(
                    current.request.client_order_id, DurableOrderStatus.REJECTED, at, reason=outcome.proof,
                )
                return True
            if outcome.acknowledgement is None:
                raise ValueError("submitted order recovery requires acknowledgement or rejection proof")
            current = self.store.transition_order(
                current.request.client_order_id,
                DurableOrderStatus.ACKNOWLEDGED,
                at,
                ack=outcome.acknowledgement,
                reason=outcome.proof,
            )
        if outcome.status is VenueOrderStatus.ACKNOWLEDGED:
            if current.status is DurableOrderStatus.UNKNOWN:
                if outcome.acknowledgement is None:
                    raise ValueError("acknowledged recovery requires acknowledgement")
                self.store.transition_order(
                    current.request.client_order_id,
                    DurableOrderStatus.ACKNOWLEDGED,
                    at,
                    ack=outcome.acknowledgement,
                    reason=outcome.proof,
                )
            return True
        if outcome.status in {VenueOrderStatus.PARTIALLY_FILLED, VenueOrderStatus.FILLED}:
            if not outcome.executions:
                raise ValueError("filled recovery requires recovered execution facts")
            for execution in outcome.executions:
                self.ingestion.ingest(
                    execution.execution,
                    external_key=execution.external_key,
                    client_order_id=current.request.client_order_id,
                    fully_filled=execution.fully_filled,
                    cursor_name=execution.cursor_name,
                    cursor_value=execution.cursor_value,
                )
            final = self.store.order(current.request.client_order_id)
            expected = (
                DurableOrderStatus.FILLED
                if outcome.status is VenueOrderStatus.FILLED
                else DurableOrderStatus.PARTIALLY_FILLED
            )
            if final is None or final.status is not expected:
                raise ValueError("recovered executions do not prove the reported final order status")
            return True
        if outcome.status is VenueOrderStatus.CANCELLED:
            self.store.transition_order(
                current.request.client_order_id, DurableOrderStatus.CANCELLED, at, reason=outcome.proof,
            )
            return True
        if outcome.status is VenueOrderStatus.EXPIRED:
            self.store.transition_order(
                current.request.client_order_id, DurableOrderStatus.EXPIRED, at, reason=outcome.proof,
            )
            return True
        if outcome.status is VenueOrderStatus.REJECTED:
            self.store.transition_order(
                current.request.client_order_id, DurableOrderStatus.REJECTED, at, reason=outcome.proof,
            )
            return True
        return False
