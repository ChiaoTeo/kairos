from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kairospy.data import DatasetStore
from kairospy.surface.cli.main import main


class DataProductExperienceTests(unittest.TestCase):
    def test_file_dataset_workflow_uses_dataset_store_terms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "signals.csv"
            source.write_text(
                "event_time,instrument_id,signal\n"
                "2026-01-01T00:00:00+00:00,BTC,1\n",
                encoding="utf-8",
            )

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "add", str(source), "--name", "research.signal", "--time", "event_time",
                ]), 0)
                added = json.loads(output.getvalue())

            self.assertEqual(added["dataset"], "research.signal")
            self.assertEqual(added["historical"]["status"], "ready")
            self.assertTrue((root / "datasets" / "research" / "signal" / "data").exists())
            self.assertFalse((root / "releases").exists())
            self.assertFalse((root / "current.ref").exists())

            for action in ("search", "describe", "doctor", "metadata", "diagnostics"):
                with self.subTest(action=action):
                    args = ["--lake-root", directory, "--format", "json", "data", action]
                    if action in {"describe", "doctor", "metadata"}:
                        args.append("research.signal")
                    with StringIO() as output, redirect_stdout(output):
                        self.assertEqual(main(args), 0)
                        payload = json.loads(output.getvalue())
                    encoded = json.dumps(payload, sort_keys=True)
                    self.assertNotIn("release_id", encoded)
                    self.assertNotIn("content_hash", encoded)
                    self.assertNotIn("quality_level", encoded)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "query", "research.signal", "--limit", "1",
                ]), 0)
                queried = json.loads(output.getvalue())
            self.assertEqual(queried["rows"][0]["signal"], 1)

            tmp = root / "datasets" / "research" / "signal" / "tmp" / "stale"
            tmp.mkdir(parents=True)
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main(["--lake-root", directory, "--format", "json", "data", "repair-index"]), 0)
                repaired = json.loads(output.getvalue())
            self.assertEqual(repaired["status"], "rebuilt")
            self.assertTrue((root / "index" / "cache.sqlite3").exists())

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json", "data", "clean-tmp", "--dataset", "research.signal",
                ]), 0)
                cleaned = json.loads(output.getvalue())
            self.assertEqual(cleaned["count"], 1)
            self.assertFalse(tmp.exists())

    def test_data_resolve_explains_stream_product_and_dataset_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "resolve", "binance_swap_btcusdt.orderbook",
                ]), 0)
                payload = json.loads(output.getvalue())

            self.assertEqual(payload["stream"], "binance_swap_btcusdt.orderbook")
            self.assertEqual(payload["space"], "binance_swap_btcusdt")
            self.assertEqual(payload["plan"]["provider"], "binance")
            self.assertEqual(payload["plan"]["product_key"], "binance.orderbook")
            self.assertEqual(payload["compatible_dataset"], "market.orderbook.crypto.binance.usdm-perpetual.btc-usdt")
            self.assertIn("/datasets/binance_swap_btcusdt/orderbook/", payload["storage"]["data"])

    def test_data_resolve_uses_alias_as_stream_to_existing_canonical_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            canonical = "market.orderbook.crypto.binance.spot.btc-usdt"
            store = DatasetStore(directory)
            store.ensure_dataset(canonical)
            store.alias(canonical, "binance_spot_btcusdt.orderbook")

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "resolve", "binance_spot_btcusdt.orderbook",
                ]), 0)
                payload = json.loads(output.getvalue())

            self.assertEqual(payload["stream"], "binance_spot_btcusdt.orderbook")
            self.assertEqual(payload["dataset"], canonical)
            self.assertEqual(payload["storage"]["source"], "alias")
            self.assertEqual(payload["plan"]["dataset"], canonical)

    def test_data_resolve_keeps_legacy_product_key_as_compatible_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "resolve", "hyperliquid.perpetual.funding",
                ]), 0)
                payload = json.loads(output.getvalue())

            self.assertEqual(payload["plan"]["source"], "legacy_product_key")
            self.assertEqual(payload["plan"]["product_key"], "hyperliquid.perpetual.funding")
            self.assertEqual(payload["plan"]["dataset"], "market.funding.crypto.hyperliquid.perpetual")

    def test_data_import_and_read_use_stream_language_over_dataset_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "momentum.csv"
            source.write_text(
                "event_time,signal\n"
                "2026-01-01T00:00:00+00:00,1\n",
                encoding="utf-8",
            )

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "import", "my_research.momentum_1h", str(source), "--time", "event_time",
                ]), 0)
                imported = json.loads(output.getvalue())
            self.assertEqual(imported["stream"], "my_research.momentum_1h")
            self.assertEqual(imported["dataset"], "my_research.momentum_1h")
            self.assertTrue((root / "datasets" / "my_research" / "momentum_1h" / "data").exists())

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "read", "my_research.momentum_1h", "--limit", "1",
                ]), 0)
                read = json.loads(output.getvalue())
            self.assertEqual(read["stream"], "my_research.momentum_1h")
            self.assertEqual(read["rows"][0]["signal"], 1)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "read", "my_research.*", "--limit", "1",
                ]), 0)
                patterned = json.loads(output.getvalue())
            self.assertEqual(list(patterned["streams"]), ["my_research.momentum_1h"])
            self.assertEqual(patterned["streams"]["my_research.momentum_1h"]["rows"][0]["signal"], 1)

    def test_data_delete_stream_data_removes_history_window_without_dataset_terms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "momentum.csv"
            source.write_text(
                "event_time,signal\n"
                "2026-01-01T00:00:00+00:00,1\n"
                "2026-01-01T01:00:00+00:00,2\n"
                "2026-01-01T02:00:00+00:00,3\n",
                encoding="utf-8",
            )

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "import", "my_research.momentum_1h", str(source), "--time", "event_time",
                ]), 0)
                output.getvalue()

            tmp = root / "datasets" / "my_research" / "momentum_1h" / "tmp" / "delete-test"
            tmp.mkdir(parents=True)
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "clean-tmp", "--stream", "my_research.momentum_1h",
                ]), 0)
                cleaned = json.loads(output.getvalue())
            self.assertEqual(cleaned["stream"], "my_research.momentum_1h")
            self.assertEqual(cleaned["count"], 1)
            self.assertFalse(tmp.exists())

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "delete-stream-data", "my_research.momentum_1h",
                    "--start", "2026-01-01T01:00:00+00:00",
                    "--end", "2026-01-01T02:00:00+00:00",
                    "--time", "event_time",
                ]), 0)
                deleted = json.loads(output.getvalue())
            self.assertEqual(deleted["stream"], "my_research.momentum_1h")
            self.assertEqual(deleted["deleted_rows"], 1)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "read", "my_research.momentum_1h", "--limit", "5",
                ]), 0)
                read = json.loads(output.getvalue())
            self.assertEqual([row["signal"] for row in read["rows"]], [1, 3])

    def test_data_replace_window_rewrites_stream_history_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "momentum.csv"
            source.write_text(
                "event_time,signal\n"
                "2026-01-01T00:00:00+00:00,1\n"
                "2026-01-01T01:00:00+00:00,2\n"
                "2026-01-01T02:00:00+00:00,3\n",
                encoding="utf-8",
            )
            replacement = root / "replacement.csv"
            replacement.write_text(
                "event_time,signal\n"
                "2026-01-01T01:00:00+00:00,20\n",
                encoding="utf-8",
            )

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "import", "my_research.momentum_1h", str(source), "--time", "event_time",
                ]), 0)
                output.getvalue()

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "replace-window", "my_research.momentum_1h", str(replacement),
                    "--start", "2026-01-01T01:00:00+00:00",
                    "--end", "2026-01-01T02:00:00+00:00",
                    "--time", "event_time",
                ]), 0)
                replaced = json.loads(output.getvalue())
            self.assertEqual(replaced["stream"], "my_research.momentum_1h")
            self.assertEqual(replaced["replaced_rows"], 1)
            self.assertEqual(replaced["inserted_rows"], 1)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "read", "my_research.momentum_1h", "--limit", "5",
                ]), 0)
                read = json.loads(output.getvalue())
            self.assertEqual([row["signal"] for row in read["rows"]], [1, 20, 3])

    def test_data_get_resolves_stream_to_historical_product_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "get", "hyperliquid_perp_btc.ohlcv_1h",
                    "--start", "2026-01-01T00:00:00+00:00",
                    "--end", "2026-01-02T00:00:00+00:00",
                    "--dry-run",
                ]), 0)
                payload = json.loads(output.getvalue())

            self.assertEqual(payload["operation"], "get")
            self.assertEqual(payload["stream"], "hyperliquid_perp_btc.ohlcv_1h")
            self.assertEqual(payload["data_product"], "hyperliquid.perpetual.ohlcv.1h")
            self.assertEqual(payload["source_plan"]["instrument"], "BTC")
            self.assertEqual(payload["historical"]["status"], "planned")
            self.assertEqual(payload["compatible_dataset"], "market.ohlcv.crypto.hyperliquid.perpetual.1h")

    def test_data_get_acquires_historical_product_and_reads_by_stream_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def fake_info(_client, request):
                self.assertEqual(request["type"], "candleSnapshot")
                self.assertEqual(request["req"]["coin"], "BTC")
                return [{
                    "t": 1767225600000,
                    "T": 1767229200000,
                    "i": "1h",
                    "o": "100",
                    "h": "110",
                    "l": "90",
                    "c": "105",
                    "v": "12.5",
                    "n": 42,
                }]

            with patch("kairospy.integrations.connectors.hyperliquid.market_data.HyperliquidInfoClient.info", fake_info):
                with StringIO() as output, redirect_stdout(output):
                    self.assertEqual(main([
                        "--lake-root", directory, "--format", "json",
                        "data", "get", "hyperliquid_perp_btc.ohlcv_1h",
                        "--start", "2026-01-01T00:00:00+00:00",
                        "--end", "2026-01-02T00:00:00+00:00",
                    ]), 0)
                    payload = json.loads(output.getvalue())

            self.assertEqual(payload["historical"]["status"], "ready")
            self.assertEqual(payload["historical"]["row_count"], 1)
            self.assertEqual(payload["stream_alias"]["stream"], "hyperliquid_perp_btc.ohlcv_1h")
            self.assertEqual(payload["stream_alias"]["dataset"], "market.ohlcv.crypto.hyperliquid.perpetual.btc.1h")
            self.assertEqual(payload["compatible_dataset"], "market.ohlcv.crypto.hyperliquid.perpetual.1h")
            self.assertEqual(payload["stream_materialization"]["source_dataset"], "market.ohlcv.crypto.hyperliquid.perpetual.1h")

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "read", "hyperliquid_perp_btc.ohlcv_1h", "--limit", "1",
                ]), 0)
                read = json.loads(output.getvalue())
            self.assertEqual(read["dataset"], "market.ohlcv.crypto.hyperliquid.perpetual.btc.1h")
            self.assertEqual(read["rows"][0]["coin"], "BTC")
            self.assertEqual(read["rows"][0]["close"], 105.0)

    def test_data_get_can_plan_full_market_archive_fanout_to_stream(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "get", "binance_swap_btcusdt.ohlcv_1h",
                    "--start", "2026-01-01T00:00:00+00:00",
                    "--end", "2026-01-02T00:00:00+00:00",
                    "--dry-run",
                ]), 0)
                payload = json.loads(output.getvalue())

            self.assertEqual(payload["operation"], "get")
            self.assertEqual(payload["stream"], "binance_swap_btcusdt.ohlcv_1h")
            self.assertEqual(payload["data_product"], "market.ohlcv.crypto.binance.usdm-perpetual.1h")
            self.assertEqual(payload["source_plan"]["fanout_target_stream"], "binance_swap_btcusdt.ohlcv_1h")
            self.assertEqual(payload["historical"]["status"], "planned")

    def test_data_get_acquires_binance_archive_rows_to_stream_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def fake_fetch(_archive, symbols, start, end, source_root):
                self.assertEqual(symbols, ("BTCUSDT",))
                self.assertLess(start, end)
                self.assertTrue(str(source_root).endswith("/source"))
                from datetime import datetime, timezone

                return [{
                    "symbol": "BTCUSDT",
                    "period_start": datetime(2026, 1, 1, tzinfo=timezone.utc),
                    "open": "100",
                    "high": "110",
                    "low": "90",
                    "close": "105",
                    "volume": "12.5",
                    "close_timestamp": 1767229199999,
                    "quote_volume": "1312.5",
                    "trade_count": 42,
                    "taker_buy_base_volume": "6.0",
                    "taker_buy_quote_volume": "630",
                }]

            with patch(
                "kairospy.integrations.connectors.binance.historical_archive."
                "BinanceUsdmPerpetualHourlyArchiveProvider.fetch",
                fake_fetch,
            ):
                with StringIO() as output, redirect_stdout(output):
                    self.assertEqual(main([
                        "--lake-root", directory, "--format", "json",
                        "data", "get", "binance_swap_btcusdt.ohlcv_1h",
                        "--start", "2026-01-01T00:00:00+00:00",
                        "--end", "2026-01-02T00:00:00+00:00",
                    ]), 0)
                    payload = json.loads(output.getvalue())

            self.assertEqual(payload["historical"]["status"], "ready")
            self.assertEqual(payload["historical"]["row_count"], 1)
            self.assertEqual(payload["stream_alias"]["stream"], "binance_swap_btcusdt.ohlcv_1h")
            self.assertEqual(payload["stream_alias"]["dataset"], "market.ohlcv.crypto.binance.usdm-perpetual.btc-usdt.1h")
            self.assertEqual(payload["compatible_dataset"], "market.ohlcv.crypto.binance.usdm-perpetual.1h")
            self.assertEqual(payload["stream_materialization"]["source_dataset"], "market.ohlcv.crypto.binance.usdm-perpetual.1h")

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "read", "binance_swap_btcusdt.ohlcv_1h", "--limit", "1",
                ]), 0)
                read = json.loads(output.getvalue())
            self.assertEqual(read["dataset"], "market.ohlcv.crypto.binance.usdm-perpetual.btc-usdt.1h")
            self.assertEqual(read["rows"][0]["symbol"], "BTCUSDT")
            self.assertEqual(read["rows"][0]["close"], 105.0)

    def test_data_probe_resolves_stream_to_live_source_plan_without_connecting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "probe", "binance_swap_btcusdt.orderbook",
                    "--limit", "2",
                    "--dry-run",
                ]), 0)
                payload = json.loads(output.getvalue())

            self.assertEqual(payload["operation"], "probe")
            self.assertEqual(payload["status"], "planned")
            self.assertEqual(payload["stream"], "binance_swap_btcusdt.orderbook")
            self.assertEqual(payload["source"], "binance.orderbook")
            self.assertEqual(payload["source_plan"]["instrument"], "BTCUSDT")
            self.assertEqual(payload["source_plan"]["market"], "usdm")

    def test_removed_commands_report_removed_instead_of_release_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            commands = [
                ("releases", ["data", "releases"]),
                ("freeze", ["data", "freeze", "--workspace", "w", "--dataset", "d", "--output", str(Path(directory) / "x.json")]),
                ("compare", ["data", "compare", "--first", "a", "--second", "b"]),
                ("audit-artifact", ["data", "audit-artifact", "--artifact", str(Path(directory) / "artifact.json")]),
            ]
            for action, args in commands:
                with self.subTest(action=action):
                    with StringIO() as output, redirect_stdout(output):
                        self.assertEqual(main(["--lake-root", directory, "--format", "json", *args]), 2)
                        payload = json.loads(output.getvalue())
                    self.assertEqual(payload["status"], "removed")
                    self.assertNotIn("ready_for_workspace", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
