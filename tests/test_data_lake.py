from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT
from kairospy.data import DatasetClient, DatasetStore, DatasetWriter


class DataLakeTest(unittest.TestCase):
    def test_default_client_uses_project_lake_root(self) -> None:
        self.assertEqual(DatasetClient().root, Path(DEFAULT_LAKE_ROOT))

    def test_store_discovers_datasets_from_file_tree_not_registry(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            writer = DatasetWriter(root)
            writer.append(
                "market.ohlcv.crypto.test.btc-usdt.1d",
                [{"event_time": "2026-01-01T00:00:00+00:00", "instrument_id": "BTCUSDT", "close": 100}],
                time_field="event_time",
                partition_by=("event_day",),
            )
            dataset = root / "datasets" / "market" / "ohlcv" / "crypto" / "test" / "btc-usdt" / "1d"
            (dataset / "dataset.json").write_text(
                json.dumps({"dataset": "market.ohlcv.crypto.test.btc-usdt.1d", "primary_time": "event_time"}),
                encoding="utf-8",
            )

            store = DatasetStore(root)
            self.assertIn("market.ohlcv.crypto.test.btc-usdt.1d", {str(item) for item in store.list_datasets()})
            self.assertFalse((root / "catalog").exists())
            self.assertFalse((root / "releases").exists())
            self.assertFalse((root / "current.ref").exists())

    def test_alias_is_a_short_ref_to_canonical_dataset_id(self) -> None:
        with TemporaryDirectory() as temporary:
            store = DatasetStore(temporary)
            writer = DatasetWriter(temporary)
            writer.append("research.signal", [{"event_time": "2026-01-01T00:00:00+00:00", "signal": 1}])

            store.alias("research.signal", "sig")

            self.assertEqual(str(store.resolve("sig")), "research.signal")
            self.assertEqual((Path(temporary) / "aliases" / "sig.ref").read_text(encoding="utf-8"), "research.signal\n")
            rows = DatasetClient(temporary).read("sig", output="rows")
            self.assertEqual(rows[0]["signal"], 1)

    def test_optional_dataset_json_does_not_gate_reads(self) -> None:
        with TemporaryDirectory() as temporary:
            writer = DatasetWriter(temporary)
            writer.append("research.no_metadata", [{"event_time": "2026-01-01T00:00:00+00:00", "value": 1}])
            metadata = Path(temporary) / "datasets" / "research" / "no_metadata" / "dataset.json"
            if metadata.exists():
                metadata.unlink()

            rows = DatasetClient(temporary).read("research.no_metadata", output="rows")
            self.assertEqual(rows[0]["value"], 1)


if __name__ == "__main__":
    unittest.main()
