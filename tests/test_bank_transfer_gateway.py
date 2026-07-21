from __future__ import annotations

from decimal import Decimal
import unittest
from uuid import uuid4

from kairospy.connectors.transfer import BankTransferGateway
from kairospy.trading.identity import AssetId
from kairospy.reference.identity import LocationId, RailId
from kairospy.treasury import BankTransferInstruction, FeePolicy, TransferStatus


class Gateway:
    def __init__(self):
        self.created = []

    def create_transfer(self, payload, *, idempotency_key):
        self.created.append((payload, idempotency_key))
        return {"id": "wire-1", "status": "created"}

    def get_transfer(self, provider_reference):
        return {"id": provider_reference, "status": "returned", "debited_amount": "1000", "fee_amount": "20", "fee_currency": "USD"}


class BankTransferGatewayTests(unittest.TestCase):
    def test_cash_transfer_is_disabled_by_default_and_normalizes_return(self):
        instruction = BankTransferInstruction("instruction", uuid4(), LocationId("bank:source"), "beneficiary", "masked", RailId("fedwire"), AssetId("USD"), Decimal("1000"), FeePolicy.ADD_TO_AMOUNT, "idem")
        bank_api = Gateway()
        with self.assertRaises(PermissionError):
            BankTransferGateway(bank_api).submit(instruction)
        transfer_gateway = BankTransferGateway(bank_api, enable_transfers=True)
        submitted = transfer_gateway.submit(instruction)
        self.assertEqual(submitted.status, TransferStatus.SUBMITTED)
        self.assertEqual(bank_api.created[0][1], "idem")
        returned = transfer_gateway.status("wire-1")
        self.assertEqual(returned.status, TransferStatus.RETURNED)
        self.assertEqual(returned.fee_amount, Decimal("20"))


if __name__ == "__main__":
    unittest.main()
