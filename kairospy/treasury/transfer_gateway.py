from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from kairospy.trading.identity import AssetId

from .transfer_contracts import TransferInstruction, TransferStatus


@dataclass(frozen=True, slots=True)
class TransferSubmission:
    provider_reference: str
    status: TransferStatus
    debited_amount: Decimal | None = None
    fee_amount: Decimal | None = None
    fee_asset: AssetId | None = None
    transaction_hash: str | None = None


class TransferGateway(Protocol):
    def submit(self, instruction: TransferInstruction) -> TransferSubmission: ...

    def status(self, provider_reference: str) -> TransferSubmission: ...


class SimulatedTransferGateway:
    def __init__(self) -> None:
        self.submissions: dict[str, TransferSubmission] = {}

    def submit(self, instruction: TransferInstruction) -> TransferSubmission:
        existing = self.submissions.get(instruction.idempotency_key)
        if existing is not None:
            return existing
        result = TransferSubmission(f"sim:{instruction.instruction_id}", TransferStatus.SUBMITTED)
        self.submissions[instruction.idempotency_key] = result
        return result

    def status(self, provider_reference: str) -> TransferSubmission:
        return next((item for item in self.submissions.values() if item.provider_reference == provider_reference), TransferSubmission(provider_reference, TransferStatus.FAILED))
