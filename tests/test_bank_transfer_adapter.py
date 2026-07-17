from __future__ import annotations

from decimal import Decimal
import unittest
from uuid import uuid4

from trading.adapters.transfer import BankTransferAdapter
from trading.domain.identity import AssetId
from trading.reference.identity import LocationId, RailId
from trading.treasury import BankTransferInstruction, FeePolicy, TransferStatus


class Gateway:
    def __init__(self):
        self.created = []

    def create_transfer(self, payload, *, idempotency_key):
        self.created.append((payload, idempotency_key))
        return {"id": "wire-1", "status": "created"}

    def get_transfer(self, provider_reference):
        return {"id": provider_reference, "status": "returned", "debited_amount": "1000", "fee_amount": "20", "fee_currency": "USD"}


class BankTransferAdapterTests(unittest.TestCase):
    def test_cash_transfer_is_disabled_by_default_and_normalizes_return(self):
        instruction = BankTransferInstruction("instruction", uuid4(), LocationId("bank:source"), "beneficiary", "masked", RailId("fedwire"), AssetId("USD"), Decimal("1000"), FeePolicy.ADD_TO_AMOUNT, "idem")
        gateway = Gateway()
        with self.assertRaises(PermissionError):
            BankTransferAdapter(gateway).submit(instruction)
        adapter = BankTransferAdapter(gateway, enable_transfers=True)
        submitted = adapter.submit(instruction)
        self.assertEqual(submitted.status, TransferStatus.SUBMITTED)
        self.assertEqual(gateway.created[0][1], "idem")
        returned = adapter.status("wire-1")
        self.assertEqual(returned.status, TransferStatus.RETURNED)
        self.assertEqual(returned.fee_amount, Decimal("20"))


if __name__ == "__main__":
    unittest.main()
