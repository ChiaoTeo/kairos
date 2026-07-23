from __future__ import annotations

from contextlib import chdir, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kairospy.surface.cli.main import main
from kairospy.surface.product import Data
from kairospy.data import DatasetClient, DatasetStore
from kairospy.data.catalog.manifest import DataManifestError
from kairospy.integrations.data_products.catalog import BuiltInDataProductRegistry


def _json_cli(args: list[str]) -> tuple[int, dict[str, object]]:
    with StringIO() as output, redirect_stdout(output):
        code = main(args)
        text = output.getvalue()
    return code, json.loads(text) if text.strip() else {}


class DataUserAddTests(unittest.TestCase):
    def test_data_add_csv_creates_queryable_dataset_store_dataset(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            csv = root / "signals.csv"
            csv.write_text("event_time,symbol,signal\n2026-01-01T00:00:00+00:00,BTC,1\n", encoding="utf-8")

            code, added = _json_cli([
                "--lake-root", temporary, "--format", "json",
                "data", "add", str(csv), "--name", "research.signal", "--time", "event_time",
            ])
            self.assertEqual(code, 0)
            self.assertEqual(added["dataset"], "research.signal")
            self.assertEqual(added["historical"]["status"], "ready")

            code, queried = _json_cli([
                "--lake-root", temporary, "--format", "json",
                "data", "query", "research.signal", "--limit", "1",
            ])
            self.assertEqual(code, 0)
            self.assertEqual(queried["rows"][0]["signal"], 1)
            self.assertTrue((root / "datasets" / "research" / "signal" / "data").exists())

    def test_data_alias_is_the_only_short_name_path_for_built_in_outputs(self) -> None:
        with TemporaryDirectory() as temporary:
            code, connected = _json_cli([
                "--lake-root", temporary, "--format", "json",
                "data", "connect", "binance.orderbook",
                "--instrument", "BTCUSDT", "--market", "spot", "--levels", "5",
            ])
            self.assertEqual(code, 0)
            self.assertEqual(connected["dataset"], "market.orderbook.crypto.binance.spot.btc-usdt")

            code, alias = _json_cli([
                "--lake-root", temporary, "--format", "json",
                "data", "alias", "market.orderbook.crypto.binance.spot.btc-usdt", "--alias", "btc_book",
            ])
            self.assertEqual(code, 0)
            self.assertEqual(alias["alias"], "btc_book")
            self.assertEqual(str(DatasetStore(temporary).resolve("btc_book")), "market.orderbook.crypto.binance.spot.btc-usdt")

    def test_built_in_connect_rejects_custom_dataset_id(self) -> None:
        with TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "canonical Dataset IDs"):
                Data(temporary).connect(
                    "binance.orderbook",
                    as_dataset="market.orderbook.crypto.binance.custom",
                    instruments=["BTCUSDT"],
                    market="spot",
                    levels=5,
                )

    def test_manifest_rejects_dataset_name_for_built_in_products(self) -> None:
        with TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "kairos.data.toml"
            manifest.write_text(
                """
[datasets.btc_book]
kind = "live"
source = "binance.orderbook"
dataset = "custom.btc_book"
instrument = "BTCUSDT"
market = "spot"
""".strip(),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(DataManifestError, "not allowed for built-in Data products"):
                Data(temporary).apply(manifest)

    def test_builtin_registry_contains_current_realtime_and_hyperliquid_products(self) -> None:
        keys = {item.key for item in BuiltInDataProductRegistry.from_default_products().list()}
        self.assertIn("binance.orderbook", keys)
        self.assertIn("massive.trade", keys)
        self.assertIn("massive.quote", keys)
        self.assertIn("massive.aggregate", keys)
        self.assertIn("hyperliquid.perpetual.trade", keys)
        self.assertIn("hyperliquid.perpetual.orderbook", keys)
        self.assertIn("hyperliquid.perpetual.ohlcv.1h", keys)

    def test_data_start_builtin_live_omits_as_dataset(self) -> None:
        with TemporaryDirectory() as temporary:
            code, payload = _json_cli([
                "--lake-root", temporary, "--format", "json",
                "data", "start", "--kind", "live", "--source", "binance.orderbook",
                "--instrument", "BTCUSDT", "--market", "spot", "--levels", "5", "--dry-run",
            ])
            self.assertEqual(code, 0)
            command = payload["command"]
            self.assertIn("data connect binance.orderbook", command)
            self.assertNotIn("--as", command)

    def test_python_api_reads_without_release_identity(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            csv = root / "signals.csv"
            csv.write_text("event_time,value\n2026-01-01T00:00:00+00:00,7\n", encoding="utf-8")
            data = Data(temporary)
            data.add(csv, name="research.signal", time="event_time")
            rows = data.read("research.signal", output="rows")
            self.assertEqual(rows[0]["value"], 7)
            explain = DatasetClient(temporary).query("research.signal").explain()
            encoded = json.dumps(explain, sort_keys=True)
            self.assertNotIn("release_id", encoded)
            self.assertNotIn("quality_level", encoded)

    def test_apply_manifest_supports_user_file_dataset(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            csv = root / "signals.csv"
            csv.write_text("event_time,value\n2026-01-01T00:00:00+00:00,1\n", encoding="utf-8")
            manifest = root / "kairos.data.toml"
            manifest.write_text(
                """
[datasets.signal]
kind = "file"
source = "./signals.csv"
dataset = "research.signal"
time = "event_time"
""".strip(),
                encoding="utf-8",
            )
            with chdir(root):
                code, payload = _json_cli([
                    "--lake-root", temporary, "--format", "json",
                    "data", "apply", str(manifest),
                ])
            self.assertEqual(code, 0)
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(DatasetClient(temporary).read("research.signal", output="rows")[0]["value"], 1)


if __name__ == "__main__":
    unittest.main()
