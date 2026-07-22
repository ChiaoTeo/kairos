from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from kairospy.identity import AssetId
from kairospy.portfolio.treasury.transfer_gateway import TransferSubmission
from kairospy.portfolio.treasury.transfer_contracts import BankTransferInstruction, TransferInstruction, TransferStatus


class BankTransferProviderClient(Protocol):
    def create_transfer(self, payload: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]: ...
    def get_transfer(self, provider_reference: str) -> dict[str, Any]: ...


class BankTransferGateway:
    """Provider-neutral cash transfer boundary; provider payloads stay here."""

    def __init__(self, client: BankTransferProviderClient, *, enable_transfers: bool = False) -> None:
        self.client = client
        self.enable_transfers = enable_transfers

    def submit(self, instruction: TransferInstruction) -> TransferSubmission:
        if not isinstance(instruction, BankTransferInstruction):
            raise TypeError(f"bank transfer gateway does not support {type(instruction).__name__}")
        if not self.enable_transfers:
            raise PermissionError("bank transfers are disabled")
        row = self.client.create_transfer({
            "beneficiary_id": instruction.beneficiary_id,
            "account_reference": instruction.account_reference,
            "rail": instruction.rail_id.value,
            "currency": instruction.asset_id.value,
            "amount": str(instruction.amount),
            "fee_policy": instruction.fee_policy.value,
        }, idempotency_key=instruction.idempotency_key)
        return _submission(row)

    def status(self, provider_reference: str) -> TransferSubmission:
        return _submission(self.client.get_transfer(provider_reference))


def _submission(row: dict[str, Any]) -> TransferSubmission:
    reference = str(row["id"])
    status = _status(str(row.get("status", "unknown")))
    fee = Decimal(str(row["fee_amount"])) if row.get("fee_amount") is not None else None
    return TransferSubmission(
        reference, status,
        Decimal(str(row["debited_amount"])) if row.get("debited_amount") is not None else None,
        fee, AssetId(row["fee_currency"]) if fee is not None and row.get("fee_currency") else None,
    )


def _status(value: str) -> TransferStatus:
    values = {
        "created": TransferStatus.SUBMITTED,
        "pending": TransferStatus.PROCESSING,
        "processing": TransferStatus.PROCESSING,
        "settled": TransferStatus.SETTLED,
        "completed": TransferStatus.COMPLETED,
        "returned": TransferStatus.RETURNED,
        "reversed": TransferStatus.REVERSED,
        "failed": TransferStatus.FAILED,
        "cancelled": TransferStatus.CANCELLED,
        "manual_review": TransferStatus.MANUAL_REVIEW,
    }
    return values.get(value.lower(), TransferStatus.MANUAL_REVIEW)
