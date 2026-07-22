from __future__ import annotations

from kairospy.identity import InstitutionId

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from uuid import uuid4

from kairospy.identity import AccountRef, AccountType, AssetId, VenueId
from kairospy.portfolio.ledger import Ledger, LedgerBook
from kairospy.reference.identity import LocationId
from kairospy.portfolio.treasury import FeePolicy, TransferOperation, TransferStatus, TreasuryAccountingProjector, TreasuryLedgerPostingService


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)
SOURCE = LocationId("source"); DESTINATION = LocationId("destination")
SOURCE_ACCOUNT = AccountRef(InstitutionId("exchange-a"), "source", AccountType.CRYPTO_SPOT)
DESTINATION_ACCOUNT = AccountRef(InstitutionId("exchange-b"), "destination", AccountType.CRYPTO_SPOT)
TRANSIT = AccountRef(InstitutionId("treasury"), "transit", AccountType.SUB_ACCOUNT)


class TreasuryAccountingProjectionTests(unittest.TestCase):
    def _projector(self):
        ledger = Ledger()
        service = TreasuryLedgerPostingService(ledger, {SOURCE: SOURCE_ACCOUNT, DESTINATION: DESTINATION_ACCOUNT}, TRANSIT)
        return ledger, TreasuryAccountingProjector(service)

    def test_gross_fee_is_deducted_from_in_transit_without_second_source_debit(self):
        ledger, projector = self._projector()
        operation = TransferOperation("transfer-1", uuid4(), "instruction", SOURCE, DESTINATION, AssetId("USDT"), Decimal("100"), TransferStatus.COMPLETED, NOW, NOW, FeePolicy.DEDUCT_FROM_AMOUNT, Decimal("100"), Decimal("99"), Decimal("1"), AssetId("USDT"))
        projector.apply(operation)
        projector.apply(operation)
        self.assertEqual(ledger.book_balance(SOURCE_ACCOUNT, LedgerBook.CASH, AssetId("USDT")), Decimal("-100"))
        self.assertEqual(ledger.book_balance(DESTINATION_ACCOUNT, LedgerBook.CASH, AssetId("USDT")), Decimal("99"))
        self.assertEqual(ledger.book_balance(TRANSIT, LedgerBook.IN_TRANSIT, AssetId("USDT")), Decimal("0"))
        self.assertEqual(len(ledger.transactions), 3)

    def test_net_fee_is_additional_source_expense(self):
        ledger, projector = self._projector()
        operation = TransferOperation("transfer-2", uuid4(), "instruction", SOURCE, DESTINATION, AssetId("USD"), Decimal("100"), TransferStatus.COMPLETED, NOW, NOW, FeePolicy.ADD_TO_AMOUNT, Decimal("101"), Decimal("100"), Decimal("1"), AssetId("USD"))
        projector.apply(operation)
        self.assertEqual(ledger.book_balance(SOURCE_ACCOUNT, LedgerBook.CASH, AssetId("USD")), Decimal("-101"))
        self.assertEqual(ledger.book_balance(DESTINATION_ACCOUNT, LedgerBook.CASH, AssetId("USD")), Decimal("100"))


if __name__ == "__main__":
    unittest.main()
