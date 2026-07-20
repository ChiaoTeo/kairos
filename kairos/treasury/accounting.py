from __future__ import annotations

from decimal import Decimal

from kairos.domain.ledger import LedgerBook

from .transfer_contracts import FeePolicy, TransferOperation, TransferStatus
from .ledger_posting import TreasuryLedgerPostingService


_DEBITED = frozenset({
    TransferStatus.SOURCE_DEBITED, TransferStatus.IN_TRANSIT,
    TransferStatus.BROADCAST, TransferStatus.CONFIRMING, TransferStatus.CONFIRMED,
    TransferStatus.PROCESSING, TransferStatus.SETTLED,
    TransferStatus.DESTINATION_CREDITED, TransferStatus.COMPLETED,
    TransferStatus.RETURNED, TransferStatus.REVERSED,
})
_CREDITED = frozenset({TransferStatus.DESTINATION_CREDITED, TransferStatus.COMPLETED})


class TreasuryAccountingProjector:
    """Idempotently converts confirmed operation state into balanced Ledger facts."""

    def __init__(self, service: TreasuryLedgerPostingService) -> None:
        self.service = service

    def apply(self, operation: TransferOperation) -> tuple:
        posted = {transaction.reference_id for transaction in self.service.ledger.transactions}
        transactions = []
        principal = operation.requested_amount
        if operation.fee_policy is FeePolicy.DEDUCT_FROM_AMOUNT and operation.debited_amount is not None:
            principal = operation.debited_amount
        debit_reference = f"{operation.transfer_id}:source-debit"
        if operation.status in _DEBITED and debit_reference not in posted:
            transactions.append(self.service.post_source_debit(operation.transfer_id, operation.source_location_id, operation.asset_id, principal, operation.updated_at))
            posted.add(debit_reference)
        fee_reference = f"{operation.transfer_id}:fee"
        if operation.fee_amount and operation.fee_asset and fee_reference not in posted:
            if operation.fee_policy is FeePolicy.DEDUCT_FROM_AMOUNT and operation.fee_asset == operation.asset_id:
                transactions.append(self.service.post_in_transit_fee(operation.transfer_id, operation.source_location_id, operation.fee_asset, operation.fee_amount, operation.updated_at))
            else:
                transactions.append(self.service.post_transfer_fee(operation.transfer_id, operation.source_location_id, operation.fee_asset, operation.fee_amount, operation.updated_at))
            posted.add(fee_reference)
        credit_reference = f"{operation.transfer_id}:destination-credit"
        if operation.status in _CREDITED and operation.destination_location_id is not None and credit_reference not in posted:
            amount = operation.credited_amount if operation.credited_amount is not None else operation.requested_amount
            transactions.append(self.service.post_destination_credit(operation.transfer_id, operation.destination_location_id, operation.asset_id, amount, operation.updated_at))
            posted.add(credit_reference)
        return_reference = f"{operation.transfer_id}:return"
        if operation.status in {TransferStatus.RETURNED, TransferStatus.REVERSED} and return_reference not in posted:
            in_transit = self.service.ledger.book_balance(self.service.transit_account, LedgerBook.IN_TRANSIT, operation.asset_id)
            if in_transit > 0:
                transactions.append(self.service.post_return(operation.transfer_id, operation.source_location_id, operation.asset_id, min(in_transit, principal), operation.updated_at))
        return tuple(transactions)
