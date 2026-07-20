from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from kairos.domain.identity import AssetId
from kairos.reference.identity import LocationId
from kairos.treasury import (
    SQLiteTreasuryRepository, TransferObservation, TransferOperation,
    TransferOperationStore, TransferReconciliationService, TransferStatus,
)


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class TreasuryDurabilityTests(unittest.TestCase):
    def test_operation_and_provider_dedup_survive_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteTreasuryRepository(Path(directory) / "treasury.sqlite3")
            store = TransferOperationStore(repository)
            operation = TransferOperation(
                "transfer-1", uuid4(), "instruction-1", LocationId("source"), LocationId("destination"),
                AssetId("USDT"), Decimal("100"), TransferStatus.CREATED, NOW, NOW,
            )
            store.create(operation, "created")
            store.transition("transfer-1", TransferStatus.VALIDATED, NOW + timedelta(seconds=1), event_id="validated")
            store.transition("transfer-1", TransferStatus.APPROVED, NOW + timedelta(seconds=2), event_id="approved")
            store.transition("transfer-1", TransferStatus.SUBMITTED, NOW + timedelta(seconds=3), event_id="submitted", provider_event_id="submit-1", provider_reference="provider-1")

            restored = TransferOperationStore(SQLiteTreasuryRepository(repository.path))
            self.assertEqual(restored.get("transfer-1").status, TransferStatus.SUBMITTED)
            duplicate = restored.transition("transfer-1", TransferStatus.SUBMITTED, NOW + timedelta(seconds=4), event_id="duplicate", provider_event_id="submit-1")
            self.assertEqual(duplicate.status, TransferStatus.SUBMITTED)
            self.assertEqual(len(restored.events("transfer-1")), 4)

    def test_reconciliation_applies_confirmed_amounts_idempotently(self) -> None:
        store = TransferOperationStore()
        operation = TransferOperation(
            "transfer-2", uuid4(), "instruction-2", LocationId("source"), LocationId("destination"),
            AssetId("USDT"), Decimal("100"), TransferStatus.SUBMITTED, NOW, NOW, provider_reference="provider-2",
        )
        store.create(operation, "created-as-submitted")
        service = TransferReconciliationService(store)
        observation = TransferObservation("provider-event-1", "provider-2", TransferStatus.SOURCE_DEBITED, NOW + timedelta(seconds=1), debited_amount=Decimal("101"), fee_amount=Decimal("1"), fee_asset=AssetId("USDT"))
        first = service.apply("transfer-2", observation)
        second = service.apply("transfer-2", observation)
        self.assertEqual(first, second)
        self.assertEqual(first.debited_amount, Decimal("101"))
        self.assertEqual(len(store.events("transfer-2")), 2)


if __name__ == "__main__":
    unittest.main()
