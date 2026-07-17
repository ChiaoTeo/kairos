from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from trading.application.recovery import RuntimeRecoveryService
from trading.domain.identity import AssetId, InstrumentId
from trading.domain.product import FutureSpec, ContractType, ProductType
from trading.orchestration.runtime_store import SQLiteRuntimeStore
from trading.reference import EconomicProduct, InstrumentDefinition, InstrumentLifecycle, ProductId, ReferenceCatalog


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class RecoveryReferenceTests(unittest.TestCase):
    def test_empty_runtime_recovers_with_current_catalog(self) -> None:
        catalog = ReferenceCatalog(); product = ProductId("product:future:BTC"); instrument = InstrumentId("future:BTC")
        catalog.products.add(EconomicProduct(product, ProductType.FUTURE, "BTC future", NOW, currency=AssetId("USDT")))
        catalog.instruments.add(InstrumentDefinition(instrument, product, ProductType.FUTURE, FutureSpec(AssetId("BTC"), AssetId("USDT"), NOW + timedelta(days=30), Decimal("1"), ContractType.LINEAR, "index"), InstrumentLifecycle(), NOW))
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            result = RuntimeRecoveryService(store, catalog, AssetId("USD"), {}).recover(NOW)
        self.assertTrue(result.ready)


if __name__ == "__main__":
    unittest.main()
