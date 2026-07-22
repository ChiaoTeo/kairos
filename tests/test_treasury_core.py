from __future__ import annotations

from kairospy.identity import InstitutionId

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from uuid import uuid4

from kairospy.identity import AccountRef, AccountType, AssetId, VenueId
from kairospy.portfolio.ledger import Ledger, LedgerBook
from kairospy.reference.identity import LocationId
from kairospy.portfolio.treasury import TransferOperation, TransferOperationStore, TransferStatus, TreasuryLedgerPostingService


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)
SOURCE = LocationId("location:binance:spot")
DESTINATION = LocationId("location:binance:futures")
SOURCE_ACCOUNT = AccountRef(InstitutionId("binance"), "spot", AccountType.CRYPTO_SPOT)
DESTINATION_ACCOUNT = AccountRef(InstitutionId("binance"), "futures", AccountType.DERIVATIVES)
TRANSIT_ACCOUNT = AccountRef(InstitutionId("treasury"), "in-transit", AccountType.SUB_ACCOUNT)


class TreasuryCoreTests(unittest.TestCase):
    def test_operation_state_is_monotonic_and_provider_events_are_idempotent(self) -> None:
        store = TransferOperationStore()
        operation = TransferOperation("transfer-1", uuid4(), "instruction-1", SOURCE, DESTINATION, AssetId("USDT"), Decimal("100"), TransferStatus.CREATED, NOW, NOW)
        store.create(operation, "created")
        store.transition("transfer-1", TransferStatus.VALIDATED, NOW + timedelta(seconds=1), event_id="validated")
        store.transition("transfer-1", TransferStatus.APPROVED, NOW + timedelta(seconds=2), event_id="approved")
        submitted = store.transition("transfer-1", TransferStatus.SUBMITTED, NOW + timedelta(seconds=3), event_id="submitted", provider_event_id="provider-1")
        duplicate = store.transition("transfer-1", TransferStatus.SUBMITTED, NOW + timedelta(seconds=4), event_id="duplicate", provider_event_id="provider-1")
        self.assertEqual(duplicate, submitted)
        self.assertEqual(len(store.events("transfer-1")), 4)
        with self.assertRaises(ValueError):
            store.transition("transfer-1", TransferStatus.COMPLETED, NOW + timedelta(seconds=5), event_id="skip")

    def test_external_transfer_uses_in_transit_until_destination_credit(self) -> None:
        ledger = Ledger()
        service = TreasuryLedgerPostingService(ledger, {SOURCE: SOURCE_ACCOUNT, DESTINATION: DESTINATION_ACCOUNT}, TRANSIT_ACCOUNT)
        service.post_source_debit("transfer-2", SOURCE, AssetId("USDT"), Decimal("100"), NOW)
        self.assertEqual(ledger.book_balance(SOURCE_ACCOUNT, LedgerBook.CASH, AssetId("USDT")), Decimal("-100"))
        self.assertEqual(ledger.book_balance(TRANSIT_ACCOUNT, LedgerBook.IN_TRANSIT, AssetId("USDT")), Decimal("100"))
        self.assertEqual(ledger.book_balance(DESTINATION_ACCOUNT, LedgerBook.CASH, AssetId("USDT")), Decimal("0"))
        service.post_destination_credit("transfer-2", DESTINATION, AssetId("USDT"), Decimal("99"), NOW + timedelta(seconds=1))
        service.post_transfer_fee("transfer-2", SOURCE, AssetId("USDT"), Decimal("1"), NOW + timedelta(seconds=2))
        self.assertEqual(ledger.book_balance(DESTINATION_ACCOUNT, LedgerBook.CASH, AssetId("USDT")), Decimal("99"))
        self.assertEqual(ledger.book_balance(TRANSIT_ACCOUNT, LedgerBook.IN_TRANSIT, AssetId("USDT")), Decimal("1"))
        self.assertEqual(ledger.book_balance(SOURCE_ACCOUNT, LedgerBook.FEE_EXPENSE, AssetId("USDT")), Decimal("1"))

    def test_internal_transfer_posts_both_controlled_sides_atomically(self) -> None:
        ledger = Ledger()
        service = TreasuryLedgerPostingService(ledger, {SOURCE: SOURCE_ACCOUNT, DESTINATION: DESTINATION_ACCOUNT}, TRANSIT_ACCOUNT)
        service.post_internal_transfer("transfer-3", SOURCE, DESTINATION, AssetId("USDT"), Decimal("25"), NOW)
        self.assertEqual(ledger.book_balance(SOURCE_ACCOUNT, LedgerBook.CASH, AssetId("USDT")), Decimal("-25"))
        self.assertEqual(ledger.book_balance(DESTINATION_ACCOUNT, LedgerBook.CASH, AssetId("USDT")), Decimal("25"))


if __name__ == "__main__":
    unittest.main()
