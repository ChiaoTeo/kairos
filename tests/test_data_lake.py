from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from trading.storage.data_lake import write_daily_dataset
from trading.data import DataCatalog, DatasetRelease
from trading.data.products import BTC_IV_RV_DAILY, BTC_SPOT_DAILY


def register_managed(catalog, dataset):
    catalog.register_product(dataset.product)
    release = DatasetRelease(
        str(dataset.key), dataset.key, "1", dataset.schema_id, "1", "test", "1",
        dataset.relative_path, "parquet", "test-content-hash",
    )
    catalog.register_release(release)
    return release


class DataLakeTest(unittest.TestCase):
    def test_discovery_registers_structured_product_dimensions(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "canonical" / "market" / "dataset=options.us.massive.spxw.test"
            directory.mkdir(parents=True)
            (directory / "schema.json").write_text(json.dumps({"schema_id": "market.event_envelope.v1"}))
            (directory / "lineage.json").write_text(json.dumps({"source": {"provider": "massive"}}))
            (directory / "manifest.json").write_text(json.dumps({"generated_at": "2026-01-01T00:00:00Z"}))
            catalog = DataCatalog(root); catalog.discover()
            product = catalog.product("market.events.options.us.spxw")
            self.assertEqual(product.dimensions["underlying"], "SPX")
            self.assertEqual(product.dimensions["contract_family"], "SPXW")
            self.assertEqual(product.dimensions["venue"], "opra")

    def test_old_registry_schema_is_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = root / "catalog" / "datasets.json"; registry.parent.mkdir(parents=True)
            registry.write_text(json.dumps({"schema_version": 1, "datasets": [{"dataset_id": "market.options.us.massive.v1", "relative_path": "canonical/market/dataset=market.options.us.massive.v1", "schema_id": "market.event_envelope.v1", "layer": "canonical"}]}))
            with self.assertRaisesRegex(ValueError, "current schema version 4"):
                DataCatalog(root)

    def test_catalog_separates_canonical_and_feature_datasets(self):
        catalog = DataCatalog("/tmp/lake")
        register_managed(catalog, BTC_SPOT_DAILY); register_managed(catalog, BTC_IV_RV_DAILY)
        self.assertEqual(catalog.product(BTC_SPOT_DAILY.key).layer.value, "canonical")
        self.assertEqual(catalog.product(BTC_IV_RV_DAILY.key).layer.value, "features")
        self.assertIn("feature_set=iv_rv_v1", str(catalog.path(str(BTC_IV_RV_DAILY.key))))

    def test_product_enrichment_cannot_erase_governance_metadata(self):
        from dataclasses import replace
        catalog = DataCatalog("/tmp/non-destructive-enrichment")
        governed = replace(
            BTC_SPOT_DAILY.product, description="Governed description", owner="data-platform",
        )
        catalog.register_product(governed)
        catalog.register_product(BTC_SPOT_DAILY.product, enrich=True)
        result = catalog.product(BTC_SPOT_DAILY.key)
        self.assertEqual(result.description, "Governed description")
        self.assertEqual(result.owner, "data-platform")

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
            partition = root / "event_year=2025" / "event_month=01"
            parquet = partition / "part-00000.parquet"
            csv = partition / "part-00000.csv"
            if parquet.exists():
                self.assertFalse(csv.exists(), "new Parquet releases must not create redundant CSV sidecars")
            else:
                self.assertTrue(csv.exists())
            capabilities = json.loads((root / "capabilities.json").read_text())
            self.assertEqual(capabilities["maximum_validation_level"], 1)
