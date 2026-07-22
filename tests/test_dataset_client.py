from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kairospy.data import DatasetClient, DatasetStore, DatasetWriter, OutputFormat


def _require_pyarrow(test: unittest.TestCase) -> None:
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        test.skipTest("pyarrow optional dependency is not installed")


class DatasetClientTests(unittest.TestCase):
    def test_read_load_rows_iter_rows_and_query_share_dataset_store(self) -> None:
        _require_pyarrow(self)
        with TemporaryDirectory() as temporary:
            writer = DatasetWriter(temporary)
            writer.append("research.signal", [
                {"timestamp": "2026-01-01T00:00:00+00:00", "instrument_id": "AAPL", "value": 1.0},
                {"timestamp": "2026-01-02T00:00:00+00:00", "instrument_id": "AAPL", "value": 2.0},
                {"timestamp": "2026-01-02T00:00:00+00:00", "instrument_id": "MSFT", "value": 3.0},
            ], partition_by=("event_day",), time_field="timestamp")

            client = DatasetClient(temporary)

            rows = client.read(
                "research.signal",
                start="2026-01-02T00:00:00+00:00",
                end="2026-01-03T00:00:00+00:00",
                instruments=("AAPL",),
                columns=("value",),
                output=OutputFormat.ROWS,
            )
            all_rows = client.load_rows("research.signal", columns=("value",))
            batches = tuple(client.query("research.signal", columns=("value",)).stream(batch_size=2))

        self.assertEqual(rows, [{"value": 2.0}])
        self.assertEqual(all_rows, [{"value": 1.0}, {"value": 2.0}, {"value": 3.0}])
        self.assertEqual(sum(batch.num_rows for batch in batches), 3)

    def test_alias_live_and_metadata_are_store_operations(self) -> None:
        _require_pyarrow(self)
        with TemporaryDirectory() as temporary:
            store = DatasetStore(temporary)
            writer = DatasetWriter(store)
            writer.append("market.trade.crypto.hyperliquid.perpetual.btc", [
                {"event_time": "2026-01-01T00:00:00+00:00", "instrument_id": "BTC", "price": 100.0},
            ])
            store.ensure_dataset("market.trade.crypto.hyperliquid.perpetual.btc", metadata={
                "primary_time": "event_time",
                "fields": ["event_time", "instrument_id", "price"],
                "provider": "hyperliquid",
            })
            client = DatasetClient(temporary)
            alias_path = client.alias("market.trade.crypto.hyperliquid.perpetual.btc", "btc_trades")
            live_path = client.live("btc_trades")
            metadata = client.metadata("btc_trades")
            datasets = {item["dataset"] for item in client.list()}
            alias_target = alias_path.read_text(encoding="utf-8").strip()

        self.assertEqual(alias_target, "market.trade.crypto.hyperliquid.perpetual.btc")
        self.assertTrue(str(live_path).endswith("/live/default"))
        self.assertEqual(metadata["dataset"], "market.trade.crypto.hyperliquid.perpetual.btc")
        self.assertTrue(metadata["historical"]["configured"])
        self.assertIn("market.trade.crypto.hyperliquid.perpetual.btc", datasets)

    def test_query_explain_reports_dataset_files_without_release_identity(self) -> None:
        _require_pyarrow(self)
        with TemporaryDirectory() as temporary:
            DatasetWriter(temporary).append("research.signal", [
                {"timestamp": "2026-01-01T00:00:00+00:00", "instrument_id": "AAPL", "value": 1.0},
            ])
            explain = DatasetClient(temporary).query("research.signal", columns=("value",)).explain()

        self.assertEqual(explain["dataset"], "research.signal")
        self.assertEqual(explain["columns"], ["value"])
        self.assertEqual(explain["file_count"], 1)
        self.assertNotIn("release_id", explain)

    def test_sql_registers_dataset_tables_when_duckdb_is_available(self) -> None:
        _require_pyarrow(self)
        try:
            import duckdb  # noqa: F401
        except ImportError:
            self.skipTest("duckdb optional dependency is not installed")
        with TemporaryDirectory() as temporary:
            DatasetWriter(temporary).append("research.signal", [
                {"timestamp": "2026-01-01T00:00:00+00:00", "instrument_id": "AAPL", "value": 1.0},
                {"timestamp": "2026-01-02T00:00:00+00:00", "instrument_id": "AAPL", "value": 2.0},
            ])
            rows = DatasetClient(temporary).sql(
                "select sum(value) as total from signal",
                datasets={"signal": "research.signal"},
                output="rows",
            )

        self.assertEqual(rows, [{"total": 3.0}])


if __name__ == "__main__":
    unittest.main()
