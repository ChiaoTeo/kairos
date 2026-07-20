from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from kairos.domain.identity import AssetId

from .transfer_contracts import TransferStatus
from .state_machine import TransferOperationStore


@dataclass(frozen=True, slots=True)
class TransferObservation:
    provider_event_id: str
    provider_reference: str
    status: TransferStatus
    observed_at: datetime
    debited_amount: Decimal | None = None
    credited_amount: Decimal | None = None
    fee_amount: Decimal | None = None
    fee_asset: AssetId | None = None
    transaction_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.provider_event_id.strip() or not self.provider_reference.strip():
            raise ValueError("transfer observation identity cannot be empty")
        if self.observed_at.tzinfo is None:
            raise ValueError("transfer observation time must be timezone-aware")


class TransferReconciliationService:
    def __init__(self, store: TransferOperationStore) -> None:
        self.store = store

    def apply(self, transfer_id: str, observation: TransferObservation):
        current = self.store.get(transfer_id)
        if current.provider_reference is not None and current.provider_reference != observation.provider_reference:
            raise ValueError("provider reference mismatch")
        return self.store.transition(
            transfer_id, observation.status, observation.observed_at,
            event_id=f"{transfer_id}:provider:{observation.provider_event_id}",
            provider_event_id=observation.provider_event_id,
            provider_reference=observation.provider_reference,
            debited_amount=observation.debited_amount if observation.debited_amount is not None else current.debited_amount,
            credited_amount=observation.credited_amount if observation.credited_amount is not None else current.credited_amount,
            fee_amount=observation.fee_amount if observation.fee_amount is not None else current.fee_amount,
            fee_asset=observation.fee_asset if observation.fee_asset is not None else current.fee_asset,
            transaction_hash=observation.transaction_hash if observation.transaction_hash is not None else current.transaction_hash,
        )
