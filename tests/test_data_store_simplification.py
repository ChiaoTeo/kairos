from __future__ import annotations

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from kairospy.data import DataApi, DatasetClient, DatasetId, DatasetReader, DatasetStore, DatasetWriter


def _require_pyarrow(test: unittest.TestCase) -> None:
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        test.skipTest("pyarrow optional dependency is not installed")


class DataStoreSimplificationTests(unittest.TestCase):
    def test_dataset_id_maps_to_file_structure_and_alias_is_only_a_pointer(self) -> None:
        with TemporaryDirectory() as temporary:
            store = DatasetStore(temporary)
            dataset = DatasetId("market.orderbook.crypto.binance.spot.btc-usdt")

            store.ensure_dataset(dataset)
            alias_path = store.alias(dataset, "btc_book")

            self.assertEqual(
                store.dataset_path(dataset),
                Path(temporary) / "datasets" / "market" / "orderbook" / "crypto" / "binance" / "spot" / "btc-usdt",
            )
            self.assertEqual(store.resolve("btc_book"), dataset)
            self.assertEqual(alias_path.read_text(encoding="utf-8").strip(), str(dataset))

    def test_reader_reads_single_dataset_as_logical_table_without_description_files(self) -> None:
        _require_pyarrow(self)
        rows = [
            {"event_time": "2026-07-22T00:00:00+00:00", "instrument_id": "BTCUSDT", "price": 100},
            {"event_time": "2026-07-23T00:00:00+00:00", "instrument_id": "BTCUSDT", "price": 101},
            {"event_time": "2026-07-23T00:00:00+00:00", "instrument_id": "ETHUSDT", "price": 201},
        ]
        with TemporaryDirectory() as temporary:
            dataset = "market.trade.crypto.binance.spot"
            writer = DatasetWriter(temporary)
            writer.append(dataset, rows, partition_by=("event_day",))

            dataset_json = DatasetStore(temporary).layout.dataset_json_path(dataset)
            self.assertFalse(dataset_json.exists())

            reader = DatasetReader(temporary)
            result = reader.read(
                dataset,
                start="2026-07-23T00:00:00+00:00",
                end="2026-07-24T00:00:00+00:00",
                instruments=("BTCUSDT",),
                columns=("event_time", "price"),
                output="rows",
            )

            self.assertEqual(result, [{"event_time": "2026-07-23T00:00:00+00:00", "price": 101}])

    def test_writer_upsert_updates_data_directory_without_versions(self) -> None:
        _require_pyarrow(self)
        dataset = "market.ohlcv.crypto.hyperliquid.perpetual.1h"
        with TemporaryDirectory() as temporary:
            writer = DatasetWriter(temporary)
            writer.append(dataset, [
                {"period_start": "2026-07-22T00:00:00+00:00", "instrument_id": "BTC", "close": 100},
            ], partition_by=("event_day",))
            writer.upsert(dataset, [
                {"period_start": "2026-07-22T00:00:00+00:00", "instrument_id": "BTC", "close": 101},
                {"period_start": "2026-07-22T01:00:00+00:00", "instrument_id": "BTC", "close": 102},
            ], key=("instrument_id", "period_start"), partition_by=("event_day",))

            root = Path(temporary) / "datasets" / "market" / "ohlcv" / "crypto" / "hyperliquid" / "perpetual" / "1h"
            self.assertFalse((root / "releases").exists())
            self.assertFalse((root / "current.ref").exists())

            rows = DatasetReader(temporary).read(dataset, output="rows")
            self.assertEqual([row["close"] for row in rows], [101, 102])

    def test_product_owned_partition_hierarchy_is_hidden_from_reader(self) -> None:
        _require_pyarrow(self)
        dataset = "market.trade.crypto.binance.spot"
        with TemporaryDirectory() as temporary:
            DatasetWriter(temporary).append(dataset, [
                {"event_time": "2026-07-22T13:00:00+00:00", "instrument_id": "BTCUSDT", "price": 100},
                {"event_time": "2026-07-22T14:00:00+00:00", "instrument_id": "ETHUSDT", "price": 200},
            ], partition_by=("event_day", "event_hour", "instrument_bucket"))

            files = DatasetReader(temporary).scan(
                dataset,
                start="2026-07-22T13:30:00+00:00",
                end="2026-07-22T14:30:00+00:00",
            )
            self.assertEqual(len(files), 2)

            rows = DatasetClient(temporary).read(
                dataset,
                start="2026-07-22T13:30:00+00:00",
                end="2026-07-22T14:30:00+00:00",
                output="rows",
            )
            self.assertEqual(rows, [
                {"event_time": "2026-07-22T14:00:00+00:00", "instrument_id": "ETHUSDT", "price": 200},
            ])

    def test_data_api_exposes_builtin_use_and_connect_with_canonical_dataset_ids(self) -> None:
        with TemporaryDirectory() as temporary:
            data = DataApi(temporary)

            planned = data.use(
                "hyperliquid.perpetual.ohlcv.1h",
                instruments=["BTC"],
                start="2026-01-01T00:00:00+00:00",
                end="2026-01-02T00:00:00+00:00",
                dry_run=True,
            )
            self.assertEqual(planned["dataset"], "market.ohlcv.crypto.hyperliquid.perpetual.1h")

            connected = data.connect("binance.orderbook", instruments=["BTCUSDT"], market="spot")
            self.assertEqual(connected["dataset"], "market.orderbook.crypto.binance.spot.btc-usdt")
            self.assertTrue((data.live("market.orderbook.crypto.binance.spot.btc-usdt") / "state.json").exists())

            with self.assertRaisesRegex(ValueError, "canonical Dataset IDs"):
                data.connect(
                    "binance.orderbook",
                    instruments=["BTCUSDT"],
                    market="spot",
                    as_dataset="market.orderbook.crypto.binance.btc-usdt",
                )

    def test_index_cache_is_optional_and_rebuildable_from_file_tree(self) -> None:
        _require_pyarrow(self)
        with TemporaryDirectory() as temporary:
            store = DatasetStore(temporary)
            DatasetWriter(store).append("research.signal", [
                {"event_time": "2026-07-22T00:00:00+00:00", "instrument_id": "BTC", "signal": 1},
            ], partition_by=("event_day",))
            store.alias("research.signal", "sig")

            index = store.rebuild_index()
            index.unlink()

            rows = DatasetReader(store).read("sig", output="rows")
            self.assertEqual(rows[0]["signal"], 1)

            rebuilt = store.rebuild_index()
            with sqlite3.connect(rebuilt) as connection:
                datasets = connection.execute("select dataset from datasets").fetchall()
                aliases = connection.execute("select alias, dataset from aliases").fetchall()
            self.assertEqual(datasets, [("research.signal",)])
            self.assertEqual(aliases, [("sig", "research.signal")])


if __name__ == "__main__":
    unittest.main()
