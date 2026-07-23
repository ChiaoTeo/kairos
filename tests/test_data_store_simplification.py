from __future__ import annotations

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from kairospy.data import (
    DataApi,
    DataStreamId,
    DataStreamResolver,
    DatasetClient,
    DatasetId,
    DatasetReader,
    DatasetStore,
    DatasetWriter,
)


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
            from kairospy.surface.product import Data

            data = Data(temporary)

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

    def test_stream_id_is_user_facing_facade_over_dataset_store(self) -> None:
        _require_pyarrow(self)
        with TemporaryDirectory() as temporary:
            data = DataApi(temporary)
            data.append("my_research.momentum_1h", [
                {"event_time": "2026-07-22T00:00:00+00:00", "signal": 1},
            ])

            ref = data.resolve_stream("my_research.momentum_1h")
            self.assertIsInstance(ref.stream_id, DataStreamId)
            self.assertEqual(ref.to_payload(), {
                "stream": "my_research.momentum_1h",
                "space": "my_research",
                "name": "momentum_1h",
                "dataset": "my_research.momentum_1h",
                "source": "stream",
            })
            self.assertTrue(
                (Path(temporary) / "datasets" / "my_research" / "momentum_1h" / "data").exists(),
            )
            self.assertEqual(data.read("my_research.momentum_1h", output="rows")[0]["signal"], 1)

    def test_stream_resolver_preserves_alias_to_canonical_dataset_asset(self) -> None:
        _require_pyarrow(self)
        canonical = "market.orderbook.crypto.binance.spot.btc-usdt"
        with TemporaryDirectory() as temporary:
            store = DatasetStore(temporary)
            DatasetWriter(store).append(canonical, [
                {"event_time": "2026-07-22T00:00:00+00:00", "instrument_id": "BTCUSDT", "bid": 100},
            ])
            store.alias(canonical, "binance_spot_btcusdt.orderbook")

            ref = DataStreamResolver(store).resolve("binance_spot_btcusdt.orderbook")
            self.assertEqual(str(ref.stream_id), "binance_spot_btcusdt.orderbook")
            self.assertEqual(str(ref.dataset_id), canonical)
            self.assertEqual(ref.source, "alias")
            self.assertEqual(DataApi(temporary).read("binance_spot_btcusdt.orderbook", output="rows")[0]["bid"], 100)

    def test_data_api_reads_many_and_pattern_from_current_dataset_tree(self) -> None:
        _require_pyarrow(self)
        with TemporaryDirectory() as temporary:
            data = DataApi(temporary)
            data.append("binance_swap_btcusdt.ohlcv_1h", [
                {"period_start": "2026-07-22T00:00:00+00:00", "close": 100},
            ])
            data.append("binance_swap_ethusdt.ohlcv_1h", [
                {"period_start": "2026-07-22T00:00:00+00:00", "close": 200},
            ])

            many = data.read_many([
                "binance_swap_btcusdt.ohlcv_1h",
                "binance_swap_ethusdt.ohlcv_1h",
            ], output="rows")
            self.assertEqual(many["binance_swap_btcusdt.ohlcv_1h"][0]["close"], 100)
            self.assertEqual(many["binance_swap_ethusdt.ohlcv_1h"][0]["close"], 200)

            matched = data.read_pattern("binance_swap_*.ohlcv_1h", output="rows")
            self.assertEqual(sorted(matched), [
                "binance_swap_btcusdt.ohlcv_1h",
                "binance_swap_ethusdt.ohlcv_1h",
            ])

    def test_data_api_deletes_stream_data_window_through_dataset_store(self) -> None:
        _require_pyarrow(self)
        with TemporaryDirectory() as temporary:
            data = DataApi(temporary)
            data.append("binance_swap_btcusdt.ohlcv_1h", [
                {"period_start": "2026-07-22T00:00:00+00:00", "close": 100},
                {"period_start": "2026-07-22T01:00:00+00:00", "close": 101},
                {"period_start": "2026-07-22T02:00:00+00:00", "close": 102},
            ], partition_by=("event_day",), time_field="period_start")

            deleted = data.delete_data(
                "binance_swap_btcusdt.ohlcv_1h",
                start="2026-07-22T01:00:00+00:00",
                end="2026-07-22T02:00:00+00:00",
                time_field="period_start",
            )

            self.assertEqual(deleted["deleted_rows"], 1)
            rows = data.read("binance_swap_btcusdt.ohlcv_1h", output="rows")
            self.assertEqual([row["close"] for row in rows], [100, 102])

    def test_data_api_replaces_stream_window_through_dataset_store(self) -> None:
        _require_pyarrow(self)
        with TemporaryDirectory() as temporary:
            data = DataApi(temporary)
            data.append("binance_swap_btcusdt.ohlcv_1h", [
                {"period_start": "2026-07-22T00:00:00+00:00", "close": 100},
                {"period_start": "2026-07-22T01:00:00+00:00", "close": 101},
                {"period_start": "2026-07-22T02:00:00+00:00", "close": 102},
            ], partition_by=("event_day",), time_field="period_start")

            replaced = data.replace_window(
                "binance_swap_btcusdt.ohlcv_1h",
                [{"period_start": "2026-07-22T01:00:00+00:00", "close": 201}],
                start="2026-07-22T01:00:00+00:00",
                end="2026-07-22T02:00:00+00:00",
                time_field="period_start",
            )

            self.assertEqual(replaced["replaced_rows"], 1)
            self.assertEqual(replaced["inserted_rows"], 1)
            rows = data.read("binance_swap_btcusdt.ohlcv_1h", output="rows")
            self.assertEqual([row["close"] for row in rows], [100, 201, 102])

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
