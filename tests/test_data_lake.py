from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from trading.storage.data_lake import write_daily_dataset
from trading.data import DataCatalog


class DataLakeTest(unittest.TestCase):
    def test_declarative_dataset_registry_extends_catalog(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = root / "catalog" / "datasets.json"; registry.parent.mkdir(parents=True)
            registry.write_text(json.dumps({"schema_version": 1, "datasets": [{"dataset_id": "market.options.us.massive.v1", "relative_path": "canonical/market/dataset=market.options.us.massive.v1", "schema_id": "market.event_envelope.v1", "layer": "canonical"}]}))
            catalog = DataCatalog(root)
            self.assertEqual(catalog.get("market.options.us.massive.v1").schema_id, "market.event_envelope.v1")

    def test_catalog_separates_canonical_and_feature_datasets(self):
        catalog = DataCatalog("/tmp/lake")
        self.assertEqual(catalog.get(DataCatalog.BTC_SPOT_DAILY.dataset_id).layer, "canonical")
        self.assertEqual(catalog.get(DataCatalog.BTC_IV_RV_DAILY.dataset_id).layer, "features")
        self.assertIn("feature_set=iv_rv_v1", str(catalog.path(DataCatalog.BTC_IV_RV_DAILY.dataset_id)))

    def test_writes_contract_lineage_coverage_and_partition_manifest(self):
        rows = [
            {"period_start": "2025-01-01T00:00:00Z", "value": 1},
            {"period_start": "2025-01-03T00:00:00Z", "value": 2},
        ]
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "dataset"
            manifest = write_daily_dataset(root, rows, dataset_id="example.v1",
                schema={"schema_id": "example.daily.v1", "columns": {}},
                lineage={"source": {"provider": "test"}})

            coverage = json.loads((root / "coverage.json").read_text())
            self.assertEqual(manifest["rows"], 2)
            self.assertEqual(coverage["coverage"]["end"], "2025-01-04T00:00:00Z")
            self.assertEqual(coverage["missing_ranges"][0]["start"], "2025-01-02T00:00:00Z")
            self.assertTrue((root / "event_year=2025" / "event_month=01" / "part-00000.csv").exists())
            capabilities = json.loads((root / "capabilities.json").read_text())
            self.assertEqual(capabilities["maximum_validation_level"], 1)
