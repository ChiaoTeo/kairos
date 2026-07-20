from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from kairos.data import (
    DataCatalog,
    DatasetKey,
    DatasetLayer,
    DataProductDefinition,
    DatasetRelease,
    DatasetStorageKind,
)


class DataStorageContractTests(unittest.TestCase):
    def test_storage_kind_round_trips_as_an_explicit_release_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = DataCatalog(directory)
            product = DataProductDefinition(
                DatasetKey("market.events.test.explicit"), "Explicit events", DatasetLayer.CANONICAL,
            )
            release = DatasetRelease(
                "release-1", product.key, "1", "custom.schema", "1", "test", "1",
                "nonstandard/location", "parquet", "hash",
                storage_kind=DatasetStorageKind.MARKET_EVENTS, layout_version="2",
            )
            catalog.register_product(product)
            catalog.register_release(release)
            catalog.save()

            raw = json.loads((Path(directory) / "catalog" / "datasets.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["releases"][0]["storage_kind"], "market_events")
            self.assertEqual(raw["releases"][0]["layout_version"], "2")
            self.assertEqual(DataCatalog(directory).release("release-1"), release)

    def test_previous_registry_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog" / "datasets.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "schema_version": 3,
                "aliases": {},
                "products": [{
                    "logical_key": "curated.slices.test", "title": "Slices", "layer": "curated",
                }],
                "releases": [{
                    "release_id": "previous-slices", "logical_key": "curated.slices.test",
                    "release_version": "1", "schema_id": "market_replay_dataset.v2", "schema_version": "2",
                    "transform_id": "previous", "transform_version": "1", "relative_path": "curated/previous",
                    "format": "parquet", "content_hash": "hash", "aliases": [],
                    "status": "approved_for_research", "quality_level": "Q2",
                }],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "current schema version 4"):
                DataCatalog(directory)

if __name__ == "__main__":
    unittest.main()
