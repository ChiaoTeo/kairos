from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory
import json
import unittest

from kairospy.infrastructure.storage.data_lake import write_daily_dataset
from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT
from kairospy.data import DataCatalog, DatasetRelease
from kairospy.data.client import DatasetClient
from kairospy.data.products import BTC_IV_RV_DAILY, BTC_SPOT_DAILY


def register_managed(catalog, dataset):
    catalog.register_product(dataset.product)
    release = DatasetRelease(
        str(dataset.key), dataset.key, "1", dataset.schema_id, "1", "test", "1",
        dataset.relative_path, "parquet", "test-content-hash",
    )
    catalog.register_release(release)
    return release


class DataLakeTest(unittest.TestCase):
    def test_default_catalog_and_client_use_project_lake_root(self):
        self.assertEqual(DataCatalog().root, Path(DEFAULT_LAKE_ROOT))
        self.assertEqual(DatasetClient().root, Path(DEFAULT_LAKE_ROOT))

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

    def test_discovery_generates_catalog_from_release_metadata(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = (
                root / "canonical" / "market" / "ohlcv" / "asset_class=crypto" / "venue=binance"
                / "product=usdm-perpetual" / "interval=1h" / "release=ds_release_metadata"
            )
            directory.mkdir(parents=True)
            (directory / "release.json").write_text(json.dumps({
                "release_id": "ds_release_metadata",
                "logical_key": "market.ohlcv.crypto.binance.usdm-perpetual.1h",
                "release_version": "content.abc",
                "schema_id": "market.ohlcv.v1",
                "schema_version": "1",
                "transform_id": "binance.usdm_perpetual.kline.ohlcv",
                "transform_version": "2",
                "content_hash": "abc",
                "provider": "binance",
                "venue": "binance",
                "status": "approved_for_backtest",
                "quality_level": "Q3",
                "published_at": "2026-07-20T00:00:00+00:00",
            }))
            (directory / "usage.json").write_text(json.dumps({
                "logical_key": "market.ohlcv.crypto.binance.usdm-perpetual.1h",
                "primary_time": "available_time",
                "dimensions": {"asset_class": "crypto", "venue": "binance", "frequency": "1h"},
            }))
            (directory / "capabilities.json").write_text(json.dumps({"maximum_validation_level": 3}))

            catalog = DataCatalog(root)
            discovered = catalog.discover()
            catalog.save()

            self.assertEqual(discovered[0].release_id, "ds_release_metadata")
            loaded = DataCatalog(root)
            release = loaded.release("market.ohlcv.crypto.binance.usdm-perpetual.1h")
            self.assertEqual(release.release_id, "ds_release_metadata")
            self.assertEqual(loaded.product(release.product_key).dimensions["frequency"], "1h")
            self.assertTrue((root / "catalog" / "datasets.json").exists())

    def test_catalog_save_uses_independent_temporary_files(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)

            def save_once(_index: int) -> None:
                catalog = DataCatalog(root)
                catalog.register_product(BTC_SPOT_DAILY.product)
                catalog.save()

            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(save_once, range(8)))

            loaded = DataCatalog(root)
            self.assertEqual(str(loaded.product(BTC_SPOT_DAILY.key).key), str(BTC_SPOT_DAILY.key))
            self.assertFalse(list((root / "catalog").glob("*.tmp")))

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
