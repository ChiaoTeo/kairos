from __future__ import annotations

from datetime import datetime, timezone
import unittest

from kairos.connectors.massive.reference import MassiveReferenceImporter
from kairos.domain.identity import AssetId
from kairos.reference import AssetType, ReferenceCatalog


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class MassiveReferenceAssetIdentityTests(unittest.TestCase):
    def test_equity_underlying_has_deliverable_asset_but_index_does_not(self) -> None:
        catalog = ReferenceCatalog(); importer = MassiveReferenceImporter(catalog)
        importer.import_underlyings((
            {"ticker": "AAPL", "market": "stocks", "primary_exchange": "XNAS", "list_date": "1980-12-12"},
            {"ticker": "I:SPX", "market": "indices", "primary_exchange": "CBOE", "list_date": "1957-03-04"},
        ), as_of=NOW)
        self.assertEqual(catalog.assets.get(AssetId("AAPL"), NOW).asset_type, AssetType.SECURITY)
        with self.assertRaises(LookupError):
            catalog.assets.get(AssetId("SPX"), NOW)


if __name__ == "__main__":
    unittest.main()
