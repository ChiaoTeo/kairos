from __future__ import annotations

from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from .transfer_gateway import TransferGateway
from .transfer_contracts import FeePolicy, TransferInstruction, TransferOperation, TransferStatus
from .policy import TransferPolicy
from .state_machine import TransferOperationStore


class TreasuryCoordinator:
    def __init__(self, store: TransferOperationStore, gateway: TransferGateway, policy: TransferPolicy) -> None:
        self.store = store
        self.gateway = gateway
        self.policy = policy

    def create(self, instruction: TransferInstruction, destination_location_id, asset_id, at: datetime) -> TransferOperation:
        transfer_id = str(uuid5(NAMESPACE_URL, f"treasury-transfer:{instruction.idempotency_key}"))
        try:
            return self.store.get(transfer_id)
        except LookupError:
            pass
        operation = TransferOperation(
            transfer_id, instruction.intent_id, instruction.instruction_id,
            instruction.source_location_id, destination_location_id, asset_id,
            instruction.amount, TransferStatus.CREATED, at, at,
            getattr(instruction, "fee_policy", None) or FeePolicy.ADD_TO_AMOUNT,
        )
        self.store.create(operation, f"{transfer_id}:created")
        return operation

    def validate(self, transfer_id: str, instruction: TransferInstruction, at: datetime) -> TransferOperation:
        self.policy.validate(instruction)
        return self.store.transition(transfer_id, TransferStatus.VALIDATED, at, event_id=f"{transfer_id}:validated")

    def approve(self, transfer_id: str, at: datetime, *, actor: str, reason: str) -> TransferOperation:
        if not actor.strip() or not reason.strip():
            raise ValueError("transfer approval requires actor and reason")
        return self.store.transition(
            transfer_id, TransferStatus.APPROVED, at,
            event_id=f"{transfer_id}:approved:{actor}", detail=f"actor={actor};reason={reason}",
        )

    def validate_and_approve(self, transfer_id: str, instruction: TransferInstruction, at: datetime, *, actor: str | None = None, reason: str | None = None) -> TransferOperation:
        self.validate(transfer_id, instruction, at)
        if self.policy.require_approval and (actor is None or reason is None):
            raise PermissionError("transfer requires explicit approval")
        return self.approve(transfer_id, at, actor=actor or "policy", reason=reason or "automatic policy approval")

    def submit(self, transfer_id: str, instruction: TransferInstruction, at: datetime) -> TransferOperation:
        result = self.gateway.submit(instruction)
        if result.status is not TransferStatus.SUBMITTED:
            raise ValueError("transfer gateway submission did not return submitted status")
        return self.store.transition(
            transfer_id, TransferStatus.SUBMITTED, at,
            event_id=f"{transfer_id}:submitted:{result.provider_reference}",
            provider_event_id=f"submission:{result.provider_reference}",
            provider_reference=result.provider_reference,
            debited_amount=result.debited_amount,
            fee_amount=result.fee_amount,
            fee_asset=result.fee_asset,
            transaction_hash=result.transaction_hash,
        )
