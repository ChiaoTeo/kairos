from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from kairos.domain.identity import AssetId, InstrumentId, VenueId
from kairos.domain.product import EquitySpec, ProductType
from kairos.reference import ReferenceCatalog, ReferenceCatalogRepository
from tests.reference_support import publish_test_instrument


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class ReferenceRepositoryTests(unittest.TestCase):
    def test_current_catalog_round_trips_as_fact_source(self) -> None:
        catalog = ReferenceCatalog()
        publish_test_instrument(catalog, InstrumentId("equity:us:AAPL"), ProductType.EQUITY, "AAPL", EquitySpec("XNAS", "US", AssetId("USD")), AssetId("USD"), VenueId("xnas"), "AAPL", NOW)
        with tempfile.TemporaryDirectory() as directory:
            repository = ReferenceCatalogRepository(Path(directory) / "catalog.json")
            repository.save(catalog)
            restored = repository.load()
        definition = restored.instruments.get(InstrumentId("equity:us:AAPL"), NOW)
        self.assertIsInstance(definition.contract_spec, EquitySpec)
        self.assertEqual(restored.active_listings(definition.instrument_id, NOW)[0].trading_symbol, "AAPL")
        self.assertEqual(restored.venues.get(VenueId("xnas"), NOW).mic, "XNAS")
        self.assertEqual(restored.validate_integrity(NOW), ())


if __name__ == "__main__":
    unittest.main()
