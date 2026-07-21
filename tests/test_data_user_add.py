from __future__ import annotations

from contextlib import chdir, redirect_stdout
import subprocess
import sys
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import UUID

from kairospy.contracts import CanonicalEventEnvelope, MarketEventKind, QuotePayload
from kairospy.trading.identity import InstrumentId
from kairospy.__main__ import main
from kairospy.market_data import CanonicalCaptureWriter
from kairospy.connectors.massive.config import MassiveConfig
from kairospy.product_surface import Data
from kairospy.data import (
    BuiltInDataProductRegistry, BuiltInHistoricalDataProtocol, DataCatalog, DataProductContract,
    DataProductDefinition, DatasetClient, DatasetKey, DatasetLayer, DatasetRelease, DatasetStatus, QualityLevel,
    SourceCacheStore, default_builtin_protocol_registry, stable_artifact_hash,
)
from kairospy.data.freshness import load_live_view_manifest, update_live_view_manifest_freshness
from kairospy.storage.data_lake import utc_midnight, write_daily_dataset


def _live_manifest_path(root: str | Path, payload: dict[str, object]) -> Path:
    directory = Path(root) / "live-views" / str(payload["dataset"]).replace(".", "/")
    manifests = sorted(directory.glob("*/manifest.json"))
    if not manifests:
        raise AssertionError(f"no live manifest found for {payload['dataset']}")
    return manifests[-1]


def _json_documents(text: str) -> list[dict[str, object]]:
    decoder = json.JSONDecoder()
    documents = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        document, index = decoder.raw_decode(text, index)
        documents.append(document)
    return documents


def _require_optional_module(testcase: unittest.TestCase, module: str, message: str) -> None:
    try:
        __import__(module)
    except ImportError:
        testcase.skipTest(message)


class DataUserAddTests(unittest.TestCase):
    def test_providers_list_uses_provider_data_product_dataset_terms(self) -> None:
        with TemporaryDirectory() as temporary:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "providers", "list",
                ]), 0)
                payload = json.loads(output.getvalue())

        self.assertEqual(payload["product"], "providers")
        self.assertEqual(payload["operation"], "list")
        self.assertIn("massive", {item["provider"] for item in payload["providers"]})
        encoded = json.dumps(payload, sort_keys=True)
        self.assertNotIn("ProviderConnector", encoded)
        self.assertNotIn("DataProductBuilder", encoded)
        self.assertNotIn("ProductSourceBinding", encoded)
        self.assertNotIn("DatasetRelease", encoded)

    def test_provider_doctor_reports_massive_daily_data_product_when_configured(self) -> None:
        with TemporaryDirectory() as temporary:
            with patch("kairospy.data.bootstrap._massive_config_for_project", return_value=MassiveConfig("test-key")):
                with StringIO() as output, redirect_stdout(output):
                    self.assertEqual(main([
                        "--lake-root", temporary, "--format", "json",
                        "providers", "doctor", "massive",
                    ]), 0)
                    payload = json.loads(output.getvalue())

        products = {item["key"]: item for item in payload["data_products"]}
        key = "market.ohlcv.equity.us.massive.1d.vendor_adjusted"
        self.assertIn(key, products)
        self.assertTrue(products[key]["available"])
        self.assertEqual(products[key]["dataset"], key)
        encoded = json.dumps(payload, sort_keys=True)
        self.assertNotIn("connector_available", encoded)
        self.assertNotIn("DatasetRelease", encoded)

    def test_data_products_plural_list_matches_usage_command(self) -> None:
        with TemporaryDirectory() as temporary:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "products", "list",
                ]), 0)
                payload = json.loads(output.getvalue())

        self.assertEqual(payload["product"], "data")
        self.assertEqual(payload["operation"], "product.list")
        self.assertIn("market.ohlcv.equity.us.massive.1d.vendor_adjusted", {
            item["key"] for item in payload["products"]
        })

    def test_data_products_doctor_resolves_alias_without_internal_terms(self) -> None:
        with TemporaryDirectory() as temporary:
            with patch("kairospy.data.bootstrap._massive_config_for_project", return_value=MassiveConfig("test-key")):
                with StringIO() as output, redirect_stdout(output):
                    self.assertEqual(main([
                        "--lake-root", temporary, "--format", "json",
                        "data", "products", "doctor", "massive.equity.ohlcv.1d",
                    ]), 0)
                    payload = json.loads(output.getvalue())

        self.assertEqual(payload["operation"], "products.doctor")
        self.assertEqual(payload["requested_key"], "massive.equity.ohlcv.1d")
        self.assertEqual(payload["key"], "market.ohlcv.equity.us.massive.1d.vendor_adjusted")
        self.assertEqual(payload["resolved_key"], "market.ohlcv.equity.us.massive.1d.vendor_adjusted")
        self.assertTrue(payload["available"])
        self.assertEqual(payload["dataset"], "market.ohlcv.equity.us.massive.1d.vendor_adjusted")
        encoded = json.dumps(payload, sort_keys=True)
        self.assertNotIn("ProviderConnector", encoded)
        self.assertNotIn("DataProductBuilder", encoded)
        self.assertNotIn("ProductSourceBinding", encoded)
        self.assertNotIn("DatasetRelease", encoded)

    def test_data_products_doctor_reports_unknown_product(self) -> None:
        with TemporaryDirectory() as temporary:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "products", "doctor", "missing.product",
                ]), 2)
                payload = json.loads(output.getvalue())

        self.assertEqual(payload["status"], "unknown_data_product")
        self.assertEqual(payload["issues"][0]["code"], "unknown_data_product")
        self.assertEqual(payload["next_commands"], ["kairospy data products list"])

    def test_provider_doctor_includes_external_process_extension(self) -> None:
        with TemporaryDirectory() as temporary:
            config = Path(temporary) / "connectors.json"
            config.write_text(json.dumps({
                "provider_extensions": [{
                    "kind": "external_process",
                    "provider": "demo-process",
                    "venue": "test",
                    "command": [sys.executable, "-c", "print('{}')"],
                    "products": [{
                        "logical_key": "market.demo.process.signal",
                        "title": "Demo process signal",
                        "primary_time": "period_start",
                        "fields": ["period_start", "symbol", "value"],
                    }],
                }],
            }), encoding="utf-8")
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "providers", "doctor", "demo-process",
                    "--provider-config", str(config),
                ]), 0)
                provider_payload = json.loads(output.getvalue())
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "products", "doctor", "market.demo.process.signal",
                    "--provider-config", str(config),
                ]), 0)
                product_payload = json.loads(output.getvalue())

        self.assertEqual(provider_payload["provider"], "demo-process")
        self.assertEqual(provider_payload["status"], "available")
        self.assertEqual(provider_payload["data_products"][0]["key"], "market.demo.process.signal")
        self.assertTrue(product_payload["available"])
        self.assertEqual(product_payload["provider"], "demo-process")

    def test_data_acquire_reports_dataset_without_release_id_for_external_process(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider_script = root / "external_provider.py"
            provider_script.write_text(
                "\n".join([
                    "import json",
                    "from pathlib import Path",
                    "__import__('sys').stdin.read()",
                    "rows = Path('rows.csv')",
                    "rows.write_text(",
                    "    'period_start,symbol,value\\n'",
                    "    '2026-01-01T00:00:00Z,AAPL,1.2\\n',",
                    "    encoding='utf-8',",
                    ")",
                    "print(json.dumps({",
                    "    'artifact_kind': 'source',",
                    "    'files': [{'path': str(rows)}],",
                    "    'fields': ['period_start', 'symbol', 'value'],",
                    "    'row_count': 1,",
                    "}))",
                    "",
                ]),
                encoding="utf-8",
            )
            config = root / "connectors.json"
            config.write_text(json.dumps({
                "provider_extensions": [{
                    "kind": "external_process",
                    "provider": "demo-process",
                    "venue": "test",
                    "command": [sys.executable, provider_script.name],
                    "products": [{
                        "logical_key": "market.demo.process.signal",
                        "title": "Demo process signal",
                        "primary_time": "period_start",
                        "fields": ["period_start", "symbol", "value"],
                    }],
                }],
            }), encoding="utf-8")

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "acquire",
                    "--dataset", "market.demo.process.signal",
                    "--start", "2026-01-01T00:00:00+00:00",
                    "--end", "2026-01-02T00:00:00+00:00",
                    "--provider", "demo-process",
                    "--venue", "test",
                    "--connector-config", str(config),
                    "--instrument", "AAPL",
                    "--yes",
                ]), 0)
                documents = _json_documents(output.getvalue())

        payload = documents[-1]
        self.assertEqual(payload["operation"], "acquire")
        self.assertEqual(payload["dataset"], "market.demo.process.signal")
        self.assertEqual(payload["status"], "ready_for_workspace")
        self.assertEqual(payload["provider"], "demo-process")
        self.assertNotIn("release_id", payload)
        self.assertNotIn("DatasetRelease", json.dumps(payload))

    def test_data_use_accepts_configured_external_process_product(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider_script = root / "external_provider.py"
            provider_script.write_text(
                "\n".join([
                    "import json",
                    "from pathlib import Path",
                    "__import__('sys').stdin.read()",
                    "rows = Path('rows.csv')",
                    "rows.write_text(",
                    "    'period_start,symbol,value\\n'",
                    "    '2026-01-01T00:00:00Z,AAPL,1.2\\n',",
                    "    encoding='utf-8',",
                    ")",
                    "print(json.dumps({",
                    "    'artifact_kind': 'source',",
                    "    'files': [{'path': str(rows)}],",
                    "    'fields': ['period_start', 'symbol', 'value'],",
                    "}))",
                    "",
                ]),
                encoding="utf-8",
            )
            config = root / "connectors.json"
            config.write_text(json.dumps({
                "provider_extensions": [{
                    "kind": "external_process",
                    "provider": "demo-process",
                    "venue": "test",
                    "command": [sys.executable, provider_script.name],
                    "products": [{
                        "logical_key": "market.demo.process.signal",
                        "title": "Demo process signal",
                        "primary_time": "period_start",
                        "fields": ["period_start", "symbol", "value"],
                    }],
                }],
            }), encoding="utf-8")

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "use", "market.demo.process.signal",
                    "--as", "research.demo_process_signal",
                    "--start", "2026-01-01T00:00:00+00:00",
                    "--end", "2026-01-02T00:00:00+00:00",
                    "--provider", "demo-process",
                    "--venue", "test",
                    "--provider-config", str(config),
                    "--instrument", "AAPL",
                ]), 0)
                payload = json.loads(output.getvalue())
            release_provider = DataCatalog(temporary).release("research.demo_process_signal").provider

        self.assertEqual(payload["operation"], "use")
        self.assertEqual(payload["dataset"], "research.demo_process_signal")
        self.assertEqual(payload["data_product"], "market.demo.process.signal")
        self.assertEqual(payload["historical"]["status"], "ready")
        self.assertEqual(payload["provider"], "demo-process")
        self.assertNotIn("release_id", json.dumps(payload))
        self.assertEqual(release_provider, "demo-process")

    def test_data_help_prioritizes_product_paths_over_provider_diagnostics(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "kairospy", "data", "--help"],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("connect", completed.stdout)
        self.assertIn("reconnect", completed.stdout)
        self.assertIn("audit", completed.stdout)
        self.assertIn("product", completed.stdout)
        self.assertIn("protocol", completed.stdout)
        self.assertIn("validate", completed.stdout)
        self.assertIn("replay", completed.stdout)
        self.assertNotIn("write               write external data", completed.stdout)
        self.assertNotIn("download            download a registered Data Product", completed.stdout)
        self.assertNotIn("prepare             plan, acquire, validate", completed.stdout)
        self.assertNotIn("prepare-spxw-daily-ohlcv", completed.stdout)
        self.assertNotIn("prepare-option-daily-ohlcv", completed.stdout)
        self.assertNotIn("prepare-equity-daily-ohlcv", completed.stdout)
        self.assertNotIn("prepare-equity-hourly-ohlcv", completed.stdout)
        self.assertNotIn("acquire", completed.stdout)
        self.assertNotIn("freeze", completed.stdout)
        self.assertNotIn("live-binance", completed.stdout)
        self.assertNotIn("soak-binance", completed.stdout)
        self.assertNotIn("provider-fetch", completed.stdout)
        self.assertNotIn("==SUPPRESS==", completed.stdout)

    def test_data_use_help_prefers_provider_config_name(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "kairospy", "data", "use", "--help"],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("--provider-config", completed.stdout)
        self.assertIn("PROVIDER_CONFIG", completed.stdout)
        self.assertNotIn("--connector-config", completed.stdout)
        self.assertNotIn("CONNECTOR_CONFIG", completed.stdout)

    def test_data_start_lists_onboarding_paths_without_executing(self) -> None:
        with TemporaryDirectory() as temporary:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "start",
                ]), 0)
                payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "needs_input")
            self.assertEqual([item["kind"] for item in payload["choices"]], ["file", "connector", "product", "live"])
            self.assertFalse((Path(temporary) / "catalog" / "datasets.json").exists())

    def test_data_start_generates_file_add_command(self) -> None:
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "signals.csv"
            source.write_text("date,symbol,value\n2026-01-01,AAPL,1.2\n", encoding="utf-8")
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "start", "--kind", "file",
                    "--file", str(source), "--name", "research.my_signal",
                    "--time", "date",
                ]), 0)
                payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["command"], f"kairospy data add {source} --name research.my_signal --time date")
            self.assertEqual(payload["file"]["status"], "ready")
            self.assertTrue(payload["file"]["exists"])
            self.assertEqual(payload["missing"], [])
            self.assertFalse(payload["will_run"])
            self.assertFalse((Path(temporary) / "catalog" / "datasets.json").exists())

    def test_data_start_reports_missing_file_path_before_generating_work(self) -> None:
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "missing.csv"
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "start", "--kind", "file",
                    "--file", str(source), "--name", "research.my_signal",
                ]), 0)
                payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "needs_input")
            self.assertEqual(payload["file"]["status"], "missing")
            self.assertEqual(payload["file"]["issues"], ["file_not_found"])
            self.assertFalse((Path(temporary) / "catalog" / "datasets.json").exists())

    def test_data_apply_manifest_applies_historical_file_dataset(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "signals.csv"
            config = root / "kairos.data.toml"
            source.write_text("date,symbol,value\n2026-01-01,AAPL,1.2\n", encoding="utf-8")
            config.write_text(
                "[datasets.my_signal]\n"
                'kind = "file"\n'
                'source = "./signals.csv"\n'
                'dataset = "research.my_signal"\n',
                encoding="utf-8",
            )

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "apply", str(config),
                ]), 0)
                payload = json.loads(output.getvalue())

            self.assertEqual(payload["status"], "ready")
            self.assertTrue(payload["will_run"])
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["datasets"][0]["operation"], "add")
            self.assertEqual(payload["datasets"][0]["dataset"], "research.my_signal")
            self.assertEqual(payload["datasets"][0]["time"], "date")

    def test_data_apply_manifest_applies_live_builtin_dataset(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "kairos.data.toml"
            config.write_text(
                "[datasets.btc_orderbook]\n"
                'kind = "live"\n'
                'source = "binance.orderbook"\n'
                'dataset = "market.orderbook.crypto.binance.btc-usdt"\n'
                'account = "binance-testnet"\n'
                'instrument = "BTCUSDT"\n'
                'market = "spot"\n'
                'levels = 20\n'
                'interval = "100ms"\n'
                'for = "paper"\n',
                encoding="utf-8",
            )

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "apply", str(config),
                ]), 0)
                payload = json.loads(output.getvalue())

            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["datasets"][0]["operation"], "connect")
            self.assertEqual(payload["datasets"][0]["dataset"], "market.orderbook.crypto.binance.btc-usdt")
            self.assertEqual(payload["datasets"][0]["runtime"]["stream"], "btcusdt@depth20@100ms")
            manifest_path = _live_manifest_path(root, payload["datasets"][0])
            manifest = load_live_view_manifest(manifest_path)
            self.assertEqual(manifest.live_data_plane["market"], "spot")
            self.assertEqual(manifest.live_data_plane["levels"], 20)

    def test_data_apply_manifest_dry_run_does_not_apply_dataset(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "signals.csv"
            config = root / "kairos.data.toml"
            source.write_text("date,symbol,value\n2026-01-01,AAPL,1.2\n", encoding="utf-8")
            config.write_text(
                "[datasets.my_signal]\n"
                'kind = "file"\n'
                'source = "./signals.csv"\n'
                'dataset = "research.my_signal"\n',
                encoding="utf-8",
            )

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "apply", str(config), "--dry-run",
                ]), 0)
                payload = json.loads(output.getvalue())

            self.assertFalse(payload["will_run"])
            self.assertEqual(payload["datasets"][0]["operation"], "plan")
            self.assertFalse((root / "catalog" / "datasets.json").exists())

    def test_data_start_does_not_implicitly_apply_manifest_in_current_directory(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "signals.csv"
            config = root / "kairos.data.toml"
            source.write_text("date,symbol,value\n2026-01-01,AAPL,1.2\n", encoding="utf-8")
            config.write_text(
                "[datasets.my_signal]\n"
                'kind = "file"\n'
                'source = "./signals.csv"\n'
                'dataset = "research.my_signal"\n',
                encoding="utf-8",
            )

            with chdir(root), StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "start",
                ]), 0)
                payload = json.loads(output.getvalue())

            self.assertEqual(payload["status"], "needs_input")
            self.assertEqual([item["kind"] for item in payload["choices"]], ["file", "connector", "product", "live"])
            self.assertFalse((root / "catalog" / "datasets.json").exists())

    def test_data_add_reports_missing_file_without_traceback(self) -> None:
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "missing.csv"
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "add", str(source), "--name", "research.my_signal",
                ]), 2)
                payload = json.loads(output.getvalue())

            self.assertEqual(payload["status"], "needs_input")
            self.assertEqual(payload["issues"][0]["code"], "file_not_found")
            self.assertIn("Why", json.dumps(payload).title())
            self.assertIn("kairospy data add", payload["next_command"])
            self.assertFalse((Path(temporary) / "catalog" / "datasets.json").exists())

    def test_data_add_reports_unsupported_file_format_without_traceback(self) -> None:
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "signals.txt"
            source.write_text("date,symbol,value\n2026-01-01,AAPL,1.2\n", encoding="utf-8")
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "add", str(source), "--name", "research.my_signal",
                ]), 2)
                payload = json.loads(output.getvalue())

            self.assertEqual(payload["status"], "needs_input")
            self.assertEqual(payload["issues"][0]["code"], "unsupported_file_format")
            self.assertIn("--protocol historical", payload["next_command"])
            self.assertFalse((Path(temporary) / "catalog" / "datasets.json").exists())

    def test_data_start_generates_live_connect_command(self) -> None:
        with TemporaryDirectory() as temporary:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "start", "--kind", "live",
                    "--source", "binance.quote",
                    "--as", "market.quote.crypto.binance.btc-usdt",
                    "--account", "binance-testnet",
                    "--instrument", "BTCUSDT",
                    "--channel", "quote",
                    "--for", "paper",
                ]), 0)
                payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(
                payload["command"],
                "kairospy data connect binance.quote --as market.quote.crypto.binance.btc-usdt "
                "--account binance-testnet --channel quote --instrument BTCUSDT --for paper",
            )
            self.assertEqual(payload["target_use"], "paper")

    def test_data_start_product_command_preserves_target_use(self) -> None:
        with TemporaryDirectory() as temporary:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "start", "--kind", "product",
                    "--product", "massive.equity.ohlcv.1d",
                    "--as", "market.ohlcv.equity.us.1d",
                    "--start", "2026-01-01T00:00:00+00:00",
                    "--end", "2026-01-02T00:00:00+00:00",
                    "--for", "backtest",
                ]), 0)
                payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(
            payload["command"],
            "kairospy data use massive.equity.ohlcv.1d --as market.ohlcv.equity.us.1d "
            "--start 2026-01-01T00:00:00+00:00 --end 2026-01-02T00:00:00+00:00 --for backtest",
        )

    def test_data_start_live_orderbook_does_not_require_channel(self) -> None:
        with TemporaryDirectory() as temporary:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "start", "--kind", "live",
                    "--source", "binance.orderbook",
                    "--as", "market.orderbook.crypto.binance.btc-usdt",
                    "--account", "binance-testnet",
                    "--instrument", "BTCUSDT",
                    "--for", "paper",
                ]), 0)
                payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["missing"], [])
        self.assertEqual(
            payload["command"],
            "kairospy data connect binance.orderbook --as market.orderbook.crypto.binance.btc-usdt "
            "--account binance-testnet --instrument BTCUSDT --for paper",
        )

    def test_data_start_live_orderbook_includes_market_levels_and_interval(self) -> None:
        with TemporaryDirectory() as temporary:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "start", "--kind", "live",
                    "--source", "binance.orderbook",
                    "--as", "market.orderbook.crypto.binance.usdm.btc-usdt",
                    "--instrument", "BTCUSDT",
                    "--market", "usdm",
                    "--levels", "20",
                    "--interval", "100ms",
                ]), 0)
                payload = json.loads(output.getvalue())

        self.assertEqual(
            payload["command"],
            "kairospy data connect binance.orderbook --as market.orderbook.crypto.binance.usdm.btc-usdt "
            "--market usdm --levels 20 --interval 100ms --instrument BTCUSDT",
        )
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["missing"], [])

    def test_top_level_help_does_not_present_removed_study_product(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "kairospy", "--help"],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertNotIn("study", completed.stdout)
        self.assertIn("data", completed.stdout)

    def test_builtin_registry_exposes_foundational_data_products_only(self) -> None:
        registry = BuiltInDataProductRegistry.from_default_products()
        products = {item.key: item for item in registry.list()}

        self.assertIn("market.ohlcv.crypto.binance.btc-usdt.1d", products)
        self.assertNotIn("market.returns.equity.us.1d", products)
        self.assertNotIn("features.liquidity.equity.us.1d", products)
        self.assertEqual(products["market.ohlcv.crypto.binance.btc-usdt.1d"].source_kind, "built_in")
        self.assertEqual(products["market.ohlcv.crypto.binance.btc-usdt.1d"].capability, "historical")
        self.assertEqual(
            products["market.ohlcv.crypto.binance.btc-usdt.1d"].protocol_name,
            "built_in.historical.market.ohlcv.crypto.binance.btc-usdt.1d",
        )

    def test_builtin_protocol_registry_resolves_historical_protocol(self) -> None:
        with TemporaryDirectory() as temporary:
            products = BuiltInDataProductRegistry.from_default_products().list()
            protocols = default_builtin_protocol_registry(temporary, products)
            protocol = protocols.historical("built_in.historical.market.ohlcv.crypto.binance.btc-usdt.1d")
            self.assertIsInstance(protocol, BuiltInHistoricalDataProtocol)

    def test_data_use_builtin_historical_product_dry_run(self) -> None:
        with TemporaryDirectory() as temporary:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "use", "market.ohlcv.crypto.binance.btc-usdt.1d",
                    "--as", "research.btc_daily",
                    "--start", "2026-01-01T00:00:00+00:00",
                    "--end", "2026-01-03T00:00:00+00:00",
                    "--for", "backtest",
                    "--dry-run",
                ]), 0)
                payload = json.loads(output.getvalue())

        self.assertEqual(payload["dataset"], "research.btc_daily")
        self.assertEqual(payload["data_product"], "market.ohlcv.crypto.binance.btc-usdt.1d")
        self.assertEqual(payload["default_dataset"], "market.ohlcv.crypto.binance.btc-usdt.1d")
        self.assertNotIn("source_kind", payload)
        self.assertEqual(payload["target_use"], "backtest")
        self.assertEqual(payload["time"], "period_start")
        self.assertEqual(payload["historical"]["status"], "needs_data")
        self.assertTrue(payload["plan"]["executable"])
        self.assertNotIn("release_id", payload)
        self.assertNotIn("protocol_name", payload)
        self.assertNotIn("artifact_ref", payload)

    def test_data_product_list_exposes_builtin_products(self) -> None:
        with TemporaryDirectory() as temporary:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "product", "list",
                ]), 0)
                payload = json.loads(output.getvalue())

        products = {item["key"]: item for item in payload["products"]}
        self.assertEqual(payload["operation"], "product.list")
        self.assertIn("market.ohlcv.crypto.binance.btc-usdt.1d", products)
        self.assertEqual(products["market.ohlcv.crypto.binance.btc-usdt.1d"]["capability"], "historical")
        self.assertNotIn("source_kind", products["market.ohlcv.crypto.binance.btc-usdt.1d"])
        self.assertNotIn("protocol_name", products["market.ohlcv.crypto.binance.btc-usdt.1d"])
        self.assertNotIn("layer", products["market.ohlcv.crypto.binance.btc-usdt.1d"])
        self.assertIn("massive.equity.ohlcv.1d", products["market.ohlcv.equity.us.massive.1d.vendor_adjusted"]["aliases"])

    def test_data_product_list_text_exposes_product_discovery_fields(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "kairospy", "data", "product", "list"],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("Kairos Built-In Data Products", completed.stdout)
        self.assertIn("market.ohlcv.crypto.binance.btc-usdt.1d", completed.stdout)
        self.assertIn("Binance BTC/USDT daily OHLCV", completed.stdout)
        self.assertIn("historical", completed.stdout)
        self.assertIn("Account", completed.stdout)
        self.assertIn("Default Dataset", completed.stdout)
        self.assertIn("Aliases: massive.equity.ohlcv.1d", completed.stdout)

    def test_data_use_help_points_to_builtin_product_discovery(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "kairospy", "data", "use", "--help"],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("data products list", completed.stdout)
        self.assertIn("Data Product key or alias", completed.stdout)
        self.assertIn("product keys, titles, capabilities and account requirements", completed.stdout)

    def test_data_use_list_products_matches_product_list_text(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "kairospy", "data", "use", "--list-products"],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("Kairos Built-In Data Products", completed.stdout)
        self.assertIn("binance.orderbook", completed.stdout)
        self.assertIn("live", completed.stdout)

    def test_data_use_accepts_documented_massive_equity_ohlcv_alias(self) -> None:
        with TemporaryDirectory() as temporary:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "use", "massive.equity.ohlcv.1d",
                    "--as", "market.ohlcv.equity.us.1d",
                    "--start", "2026-01-01T00:00:00+00:00",
                    "--end", "2026-01-02T00:00:00+00:00",
                    "--for", "backtest",
                    "--dry-run",
                ]), 0)
                payload = json.loads(output.getvalue())

        self.assertEqual(payload["dataset"], "market.ohlcv.equity.us.1d")
        self.assertEqual(payload["data_product"], "market.ohlcv.equity.us.massive.1d.vendor_adjusted")
        self.assertEqual(payload["default_dataset"], "market.ohlcv.equity.us.massive.1d.vendor_adjusted")
        self.assertEqual(payload["provider"], "massive")
        self.assertEqual(payload["target_use"], "backtest")

    def test_data_use_unknown_product_reports_registered_keys_without_traceback(self) -> None:
        with TemporaryDirectory() as temporary:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "use", "unknown.product",
                    "--start", "2026-01-01T00:00:00+00:00",
                    "--end", "2026-01-02T00:00:00+00:00",
                    "--dry-run",
                ]), 2)
                payload = json.loads(output.getvalue())

        self.assertEqual(payload["status"], "needs_input")
        self.assertEqual(payload["issues"][0]["code"], "unknown_built_in_product")
        self.assertIn("massive.equity.ohlcv.1d", payload["aliases"])
        self.assertEqual(payload["next_command"], "kairospy data use --list-products")

    def test_data_use_unknown_product_lists_provider_config_products(self) -> None:
        with TemporaryDirectory() as temporary:
            config = Path(temporary) / "providers.json"
            config.write_text(json.dumps({
                "provider_extensions": [{
                    "kind": "external_process",
                    "provider": "demo-process",
                    "venue": "test",
                    "command": [sys.executable, "-c", "print('{}')"],
                    "products": [{
                        "logical_key": "market.demo.process.signal",
                        "title": "Demo process signal",
                        "primary_time": "period_start",
                        "fields": ["period_start", "symbol", "value"],
                    }],
                }],
            }), encoding="utf-8")
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "use", "unknown.product",
                    "--provider-config", str(config),
                    "--start", "2026-01-01T00:00:00+00:00",
                    "--end", "2026-01-02T00:00:00+00:00",
                    "--dry-run",
                ]), 2)
                payload = json.loads(output.getvalue())

        self.assertIn("market.demo.process.signal", payload["known_keys"])

    def test_data_connect_unknown_live_product_reports_registered_keys_without_traceback(self) -> None:
        with TemporaryDirectory() as temporary:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "connect", "unknown.product",
                    "--as", "market.quote.crypto.unknown",
                ]), 2)
                payload = json.loads(output.getvalue())

        self.assertEqual(payload["operation"], "connect")
        self.assertEqual(payload["status"], "needs_input")
        self.assertEqual(payload["issues"][0]["code"], "unknown_built_in_product")
        self.assertIn("binance.quote", payload["known_keys"])
        self.assertEqual(payload["next_command"], "kairospy data product list")

    def test_data_sample_unknown_live_product_reports_registered_keys_without_traceback(self) -> None:
        with TemporaryDirectory() as temporary:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "sample", "unknown.product",
                    "--instrument", "BTCUSDT",
                ]), 2)
                payload = json.loads(output.getvalue())

        self.assertEqual(payload["operation"], "sample")
        self.assertEqual(payload["status"], "needs_input")
        self.assertEqual(payload["issues"][0]["code"], "unknown_built_in_product")
        self.assertIn("binance.orderbook", payload["known_keys"])
        self.assertEqual(payload["next_command"], "kairospy data product list")

    def test_data_reconnect_without_live_configuration_reports_next_command(self) -> None:
        with TemporaryDirectory() as temporary:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "reconnect", "research.not_live",
                ]), 2)
                payload = json.loads(output.getvalue())

        self.assertEqual(payload["operation"], "reconnect")
        self.assertEqual(payload["status"], "needs_input")
        self.assertEqual(payload["issues"][0]["code"], "live_not_configured")
        self.assertIn("kairospy data connect", payload["next_command"])

    def test_dataset_commands_report_unknown_dataset_without_traceback(self) -> None:
        commands = (
            ("metadata", ["data", "metadata", "missing.dataset"]),
            ("validate", ["data", "validate", "missing.dataset"]),
            ("query", ["data", "query", "missing.dataset"]),
            ("replay", ["data", "replay", "missing.dataset"]),
            ("promote", ["data", "promote", "missing.dataset", "--for", "backtest"]),
        )
        with TemporaryDirectory() as temporary:
            for operation, command_args in commands:
                with self.subTest(operation=operation):
                    with StringIO() as output, redirect_stdout(output):
                        self.assertEqual(main([
                            "--lake-root", temporary, "--format", "json",
                            *command_args,
                        ]), 2)
                        payload = json.loads(output.getvalue())

                    self.assertEqual(payload["operation"], operation)
                    self.assertEqual(payload["status"], "needs_input")
                    self.assertEqual(payload["issues"][0]["code"], "dataset_not_found")
                    self.assertEqual(payload["next_command"], "kairospy data start")

    def test_data_list_shows_product_readiness_without_catalog_internals(self) -> None:
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "signals.csv"
            source.write_text("date,symbol,value\n2026-01-01,AAPL,1.2\n", encoding="utf-8")

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "add", str(source), "--name", "research.my_signal",
                ]), 0)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "list",
                ]), 0)
                payload = json.loads(output.getvalue())

            encoded = json.dumps(payload)
            self.assertEqual(payload["operation"], "list")
            self.assertEqual(payload["datasets"][0]["dataset"], "research.my_signal")
            self.assertEqual(payload["datasets"][0]["time"], "date")
            self.assertIn("workspace", payload["datasets"][0]["ready_for"])
            self.assertNotIn("layer", encoded)
            self.assertNotIn("release_id", encoded)
            self.assertNotIn("selected_release", encoded)
            self.assertNotIn("manifest_hash", encoded)
            self.assertNotIn(str(source), encoded)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary,
                    "data", "list",
                ]), 0)
                text = output.getvalue()

        self.assertIn("Dataset", text)
        self.assertIn("Status", text)
        self.assertIn("Ready For", text)
        self.assertNotIn("Layer", text)
        self.assertNotIn("Releases", text)
        self.assertNotIn("Selected", text)

    def test_historical_commands_report_live_only_dataset_without_traceback(self) -> None:
        with TemporaryDirectory() as temporary:
            connector = Path(temporary) / "my_live_protocol.py"
            connector.write_text(
                "\n".join([
                    "async def stream(request):",
                    "    if False:",
                    "        yield {}",
                    "",
                ]),
                encoding="utf-8",
            )
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "connect", str(connector),
                    "--as", "research.live_only",
                    "--time", "event_time",
                    "--instrument", "AAPL",
                ]), 0)

            commands = (
                ("validate", ["data", "validate", "research.live_only"]),
                ("query", ["data", "query", "research.live_only"]),
                ("promote", ["data", "promote", "research.live_only", "--for", "backtest"]),
            )
            for operation, command_args in commands:
                with self.subTest(operation=operation):
                    with StringIO() as output, redirect_stdout(output):
                        self.assertEqual(main([
                            "--lake-root", temporary, "--format", "json",
                            *command_args,
                        ]), 2)
                        payload = json.loads(output.getvalue())

                    self.assertEqual(payload["operation"], operation)
                    self.assertEqual(payload["status"], "needs_data")
                    self.assertEqual(payload["issues"][0]["code"], "historical_not_configured")
                    self.assertNotIn("live_view_id", json.dumps(payload))

    def test_data_lists_builtin_products(self) -> None:
        payload = Data("/tmp/kairospy-product-api-test").products()
        products = {item["key"]: item for item in payload["products"]}

        self.assertEqual(payload["operation"], "product.list")
        self.assertIn("binance.quote", products)
        self.assertEqual(products["binance.quote"]["capability"], "live")
        self.assertNotIn("source_kind", products["binance.quote"])
        self.assertNotIn("protocol_name", products["binance.quote"])
        self.assertNotIn("layer", products["binance.quote"])
        self.assertIn("binance.orderbook", products)
        self.assertEqual(products["binance.orderbook"]["default_dataset_name"], "market.orderbook.crypto.binance")

    def test_data_protocol_cli_lists_templates_and_checks_user_protocols(self) -> None:
        with TemporaryDirectory() as temporary:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "protocol", "list",
                ]), 0)
                listed = json.loads(output.getvalue())
            kinds = {item["kind"] for item in listed["protocols"]}
            self.assertEqual(kinds, {"historical", "live"})

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary,
                    "data", "protocol", "template", "--kind", "historical",
                ]), 0)
                template_text = output.getvalue()
            self.assertIn("def load(request)", template_text)

            historical = Path(temporary) / "my_signal_protocol.py"
            historical.write_text(
                "\n".join([
                    "def load(request):",
                    "    return [{",
                    "        'timestamp': '2026-01-01T00:00:00Z',",
                    "        'instrument': request.instruments[0] if request.instruments else 'AAPL',",
                    "        'value': 1.2,",
                    "    }]",
                    "",
                ]),
                encoding="utf-8",
            )
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "protocol", "check", str(historical),
                    "--kind", "historical",
                    "--name", "research.protocol_signal",
                    "--instrument", "AAPL",
                ]), 0)
                checked = json.loads(output.getvalue())

            self.assertEqual(checked["status"], "ready")
            self.assertEqual(checked["source"], "my_signal_protocol.py")
            self.assertEqual(checked["row_count"], 1)
            self.assertNotIn(str(historical), json.dumps(checked))
            self.assertIn("data add my_signal_protocol.py", checked["next_command"])

            live = Path(temporary) / "my_live_protocol.py"
            live.write_text(
                "\n".join([
                    "async def stream(request):",
                    "    if False:",
                    "        yield {}",
                    "",
                ]),
                encoding="utf-8",
            )
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "protocol", "check", str(live),
                    "--kind", "live",
                    "--name", "research.live_signal",
                ]), 0)
                live_checked = json.loads(output.getvalue())

            self.assertEqual(live_checked["status"], "ready")
            self.assertEqual(live_checked["source"], "my_live_protocol.py")
            self.assertNotIn(str(live), json.dumps(live_checked))

    def test_data_protocol_accepts_protocol_object_without_adapter_naming(self) -> None:
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "object_protocol.py"
            source.write_text(
                "\n".join([
                    "class SignalProtocol:",
                    "    def load(self, request):",
                    "        return [{'timestamp': '2026-01-01T00:00:00Z', 'symbol': 'AAPL', 'value': 1.0}]",
                    "",
                    "PROTOCOL = SignalProtocol()",
                    "",
                ]),
                encoding="utf-8",
            )
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "protocol", "check", str(source),
                    "--kind", "historical",
                    "--name", "research.object_protocol",
                ]), 0)
                payload = json.loads(output.getvalue())

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["fields"], ["timestamp", "symbol", "value"])

    def test_data_protocol_error_uses_protocol_naming_not_adapter(self) -> None:
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "empty_protocol.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "protocol", "check", str(source),
                    "--kind", "historical",
                ]), 2)
                payload = json.loads(output.getvalue())

        message = payload["issues"][0]["message"]
        self.assertIn("PROTOCOL.load", message)
        self.assertIn("get_protocol", message)
        self.assertNotIn("ADAPTER", message)
        self.assertNotIn("get_adapter", message)

    def test_data_exposes_list_metadata_replay_and_protocol(self) -> None:
        _require_optional_module(self, "pyarrow", "pyarrow optional dependency is not installed")
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "signals.csv"
            source.write_text("date,symbol,value\n2026-01-01,AAPL,1.2\n", encoding="utf-8")
            api = Data(temporary)
            api.add(source, name="research.api_signal")

            listed = api.list()
            self.assertEqual(listed["datasets"][0]["dataset"], "research.api_signal")
            self.assertNotIn("layer", json.dumps(listed))

            metadata = api.metadata("research.api_signal")
            self.assertEqual(metadata["dataset"], "research.api_signal")
            self.assertNotIn("release_id", json.dumps(metadata))

            replay = api.replay("research.api_signal", limit=1)
            self.assertEqual(replay["rows"][0]["symbol"], "AAPL")
            self.assertNotIn("release_id", json.dumps(replay))

            protocols = api.protocol("list")
            self.assertEqual({item["kind"] for item in protocols["protocols"]}, {"historical", "live"})

    def test_data_can_apply_manifest(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "signals.csv"
            config = root / "kairos.data.toml"
            source.write_text("date,symbol,value\n2026-01-01,AAPL,1.2\n", encoding="utf-8")
            config.write_text(
                "[datasets.my_signal]\n"
                'kind = "file"\n'
                'source = "./signals.csv"\n'
                'dataset = "research.api_signal"\n',
                encoding="utf-8",
            )

            payload = Data(temporary).apply(config)
            doctor = Data(temporary).doctor("research.api_signal")

        self.assertEqual(payload["operation"], "apply")
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["datasets"][0]["dataset"], "research.api_signal")
        self.assertEqual(doctor["status"], "ready_for_workspace")

    def test_data_matches_cli_dataset_naming_for_builtin_use(self) -> None:
        with TemporaryDirectory() as temporary:
            payload = Data(temporary).use(
                "market.ohlcv.crypto.binance.btc-usdt.1d",
                as_dataset="research.btc_daily",
                start="2026-01-01T00:00:00+00:00",
                end="2026-01-03T00:00:00+00:00",
                for_use="backtest",
                dry_run=True,
            )

        self.assertEqual(payload["dataset"], "research.btc_daily")
        self.assertEqual(payload["data_product"], "market.ohlcv.crypto.binance.btc-usdt.1d")
        self.assertEqual(payload["default_dataset"], "market.ohlcv.crypto.binance.btc-usdt.1d")
        self.assertEqual(payload["source_kind"], "built_in")
        self.assertEqual(payload["target_use"], "backtest")

    def test_data_connects_builtin_live_with_instruments(self) -> None:
        with TemporaryDirectory() as temporary:
            payload = Data(temporary).connect(
                "binance.quote",
                as_dataset="market.quote.crypto.binance.btc-usdt",
                channel="quote",
                instruments=("BTCUSDT",),
                freshness_seconds=7.0,
                for_use="paper",
            )
            manifest = load_live_view_manifest(_live_manifest_path(temporary, payload))

        self.assertEqual(payload["source_kind"], "built_in")
        self.assertEqual(payload["target_use"], "paper")
        self.assertNotIn("artifact", payload)
        self.assertEqual(payload["runtime"]["stream"], "btcusdt@bookTicker")
        self.assertEqual(manifest.live_data_plane["freshness"]["max_age_seconds"], 7.0)
        self.assertEqual(manifest.live_data_plane["target_use"], "paper")
        self.assertEqual(manifest.live_data_plane["stream"], "btcusdt@bookTicker")

    def test_data_connects_and_samples_binance_orderbook(self) -> None:
        class Socket:
            def __init__(self, rows):
                self.rows = iter(rows)

            def receive(self):
                return next(self.rows)

            def close(self):
                pass

        class Connector:
            def __init__(self):
                self.urls: list[str] = []

            def connect(self, url: str):
                self.urls.append(url)
                return Socket(({
                    "e": "depthUpdate",
                    "E": 1767225600000,
                    "s": "BTCUSDT",
                    "U": 10,
                    "u": 12,
                    "b": [["50000", "1"]],
                    "a": [["50001", "2"]],
                },))

        with TemporaryDirectory() as temporary:
            connected = Data(temporary).connect(
                "binance.orderbook",
                as_dataset="market.orderbook.crypto.binance.btc-usdt",
                instruments=("BTCUSDT",),
            )
            connector = Connector()
            sampled = Data(temporary).sample(
                "binance.orderbook",
                as_dataset="market.orderbook.crypto.binance.btc-usdt",
                instruments=("BTCUSDT",),
                limit=1,
                connector=connector,
            )

        self.assertEqual(connected["runtime"]["stream"], "btcusdt@depth")
        self.assertEqual(sampled["source"], "binance.orderbook")
        self.assertEqual(sampled["runtime"]["stream"], "btcusdt@depth")
        self.assertTrue(connector.urls[0].endswith("/btcusdt@depth"))
        self.assertEqual(sampled["row_count"], 1)
        self.assertEqual(sampled["rows"][0]["kind"], "order_book_delta")

    def test_data_samples_binance_usdm_orderbook_levels_in_realtime(self) -> None:
        class Socket:
            def __init__(self, rows):
                self.rows = iter(rows)

            def receive(self):
                return next(self.rows)

            def close(self):
                pass

        class Connector:
            def __init__(self):
                self.urls: list[str] = []

            def connect(self, url: str):
                self.urls.append(url)
                return Socket(({
                    "lastUpdateId": 42,
                    "bids": [["50000", "1"]],
                    "asks": [["50001", "2"]],
                },))

        with TemporaryDirectory() as temporary:
            connected = Data(temporary).connect(
                "binance.orderbook",
                as_dataset="market.orderbook.crypto.binance.usdm.btc-usdt",
                instruments=("BTCUSDT",),
                market="usdm",
                levels=20,
                interval="100ms",
            )
            connector = Connector()
            streamed_rows: list[dict[str, object]] = []
            sampled = Data(temporary).sample(
                "binance.orderbook",
                as_dataset="market.orderbook.crypto.binance.usdm.btc-usdt",
                instruments=("BTCUSDT",),
                market="usdm",
                levels=20,
                interval="100ms",
                limit=1,
                connector=connector,
            )
            reconnected = Data(temporary).reconnect(
                "market.orderbook.crypto.binance.usdm.btc-usdt",
            )
            from kairospy.product_surface import data_sample, _args
            data_sample(_args(
                temporary,
                source="binance.orderbook",
                as_dataset="market.orderbook.crypto.binance.usdm.btc-usdt",
                instrument=["BTCUSDT"],
                market="usdm",
                levels=20,
                interval="100ms",
                limit=1,
                connector=Connector(),
                channel=None,
                environment=None,
            ), on_row=streamed_rows.append)

        self.assertEqual(connected["runtime"]["market"], "usdm")
        self.assertTrue(connected["runtime"]["futures"])
        self.assertEqual(connected["runtime"]["stream"], "btcusdt@depth20@100ms")
        self.assertEqual(reconnected["runtime"]["market"], "usdm")
        self.assertEqual(reconnected["runtime"]["levels"], 20)
        self.assertEqual(reconnected["runtime"]["interval"], "100ms")
        self.assertEqual(reconnected["runtime"]["stream"], "btcusdt@depth20@100ms")
        self.assertEqual(sampled["runtime"]["levels"], 20)
        self.assertEqual(sampled["runtime"]["interval"], "100ms")
        self.assertTrue(connector.urls[0].endswith("/btcusdt@depth20@100ms"))
        self.assertIn("fstream.binance.com", connector.urls[0])
        self.assertEqual(sampled["rows"][0]["kind"], "order_book_snapshot")
        self.assertEqual(streamed_rows[0]["kind"], "order_book_snapshot")

    def test_data_sample_uses_explicit_live_source_parameters(self) -> None:
        class Socket:
            def __init__(self, rows):
                self.rows = iter(rows)

            def receive(self):
                return next(self.rows)

            def close(self):
                pass

        class Connector:
            def __init__(self):
                self.urls: list[str] = []

            def connect(self, url: str):
                self.urls.append(url)
                return Socket(({
                    "lastUpdateId": 42,
                    "bids": [["50000", "1"]],
                    "asks": [["50001", "2"]],
                },))

        with TemporaryDirectory() as temporary:
            connector = Connector()
            from kairospy.product_surface import data_sample, _args

            sampled = data_sample(_args(
                temporary,
                source="binance.orderbook",
                as_dataset="market.orderbook.crypto.binance.usdm.btc-usdt",
                instrument=["BTCUSDT"],
                market="usdm",
                levels=20,
                interval="100ms",
                limit=1,
                connector=connector,
                channel=None,
                environment=None,
            ))

        self.assertEqual(sampled["source"], "binance.orderbook")
        self.assertEqual(sampled["dataset"], "market.orderbook.crypto.binance.usdm.btc-usdt")
        self.assertEqual(sampled["runtime"]["market"], "usdm")
        self.assertEqual(sampled["runtime"]["levels"], 20)
        self.assertEqual(sampled["runtime"]["interval"], "100ms")
        self.assertTrue(connector.urls[0].endswith("/btcusdt@depth20@100ms"))

    def test_data_can_sample_live_source_with_explicit_parameters(self) -> None:
        class Socket:
            def __init__(self, rows):
                self.rows = iter(rows)

            def receive(self):
                return next(self.rows)

            def close(self):
                pass

        class Connector:
            def __init__(self):
                self.urls: list[str] = []

            def connect(self, url: str):
                self.urls.append(url)
                return Socket(({
                    "lastUpdateId": 42,
                    "bids": [["50000", "1"]],
                    "asks": [["50001", "2"]],
                },))

        with TemporaryDirectory() as temporary:
            connector = Connector()

            sampled = Data(temporary).sample(
                "binance.orderbook",
                as_dataset="market.orderbook.crypto.binance.usdm.btc-usdt",
                instruments=("BTCUSDT",),
                market="usdm",
                levels=20,
                interval="100ms",
                limit=1,
                connector=connector,
            )

        self.assertEqual(sampled["dataset"], "market.orderbook.crypto.binance.usdm.btc-usdt")
        self.assertEqual(sampled["runtime"]["stream"], "btcusdt@depth20@100ms")
        self.assertTrue(connector.urls[0].endswith("/btcusdt@depth20@100ms"))

    def test_data_sample_text_summary_shows_orderbook_parameters_without_rows(self) -> None:
        from argparse import Namespace
        from kairospy.__main__ import _emit_data_payload

        payload = {
            "product": "data",
            "operation": "sample",
            "source": "binance.orderbook",
            "dataset": "market.orderbook.crypto.binance.usdm.btc-usdt",
            "provider": "binance",
            "venue": "binance-usdm",
            "runtime": {
                "market": "usdm",
                "symbol": "BTCUSDT",
                "channel": "depth",
                "levels": 20,
                "interval": "100ms",
                "stream": "btcusdt@depth20@100ms",
            },
            "limit": 5,
            "row_count": 1,
            "rows": [{"kind": "order_book_snapshot"}],
        }
        args = Namespace(format="text", action="sample")
        with StringIO() as output, redirect_stdout(output):
            _emit_data_payload(args, "Kairos Data Sample Summary", payload)
            rendered = output.getvalue()

        self.assertIn("Market", rendered)
        self.assertIn("usdm", rendered)
        self.assertIn("Levels", rendered)
        self.assertIn("20", rendered)
        self.assertIn("btcusdt@depth20@100ms", rendered)
        self.assertNotIn("order_book_snapshot", rendered)

    def test_data_sample_rejects_non_positive_limit_before_connecting(self) -> None:
        class Connector:
            def connect(self, url: str):
                raise AssertionError("sample should validate limit before connecting")

        with TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "--limit must be positive"):
                Data(temporary).sample(
                    "binance.orderbook",
                    instruments=("BTCUSDT",),
                    limit=0,
                    connector=Connector(),
                )

    def test_data_api_is_user_entrypoint(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "signals.csv"
            source.write_text("date,symbol,value\n2026-01-01,AAPL,1.2\n", encoding="utf-8")
            api = Data(root)

            added = api.add(source, name="research.client_signal")
            used = api.use(
                "market.ohlcv.crypto.binance.btc-usdt.1d",
                as_dataset="research.client_btc_daily",
                start="2026-01-01T00:00:00+00:00",
                end="2026-01-03T00:00:00+00:00",
                dry_run=True,
            )
            live = api.connect(
                "binance.quote",
                as_dataset="market.quote.crypto.binance.client-btc-usdt",
                channel="quote",
                instruments=("BTCUSDT",),
            )

        self.assertEqual(added["dataset"], "research.client_signal")
        self.assertEqual(added["historical"]["status"], "ready_for_workspace")
        self.assertEqual(used["dataset"], "research.client_btc_daily")
        self.assertEqual(used["data_product"], "market.ohlcv.crypto.binance.btc-usdt.1d")
        self.assertEqual(used["default_dataset"], "market.ohlcv.crypto.binance.btc-usdt.1d")
        self.assertEqual(live["source_kind"], "built_in")
        self.assertEqual(live["runtime"]["stream"], "btcusdt@bookTicker")

    def test_removed_data_product_api_name_is_not_exposed(self) -> None:
        import kairospy.product_surface as product_surface

        self.assertFalse(hasattr(product_surface, "DataProductApi"))
        self.assertFalse(hasattr(Data, "start_config"))
        self.assertFalse(hasattr(Data, "sample_config"))

    def test_data_reader_produces_dataset_client_for_consumption_only(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "signals.csv"
            source.write_text("date,symbol,value\n2026-01-01,AAPL,1.2\n", encoding="utf-8")
            data = Data(root)
            data.add(source, name="research.client_signal")

            reader = data.reader()

        self.assertIsInstance(reader, DatasetClient)
        self.assertEqual(reader.root, root)
        self.assertFalse(hasattr(reader, "add_file"))
        self.assertFalse(hasattr(reader, "use_product"))
        self.assertFalse(hasattr(reader, "connect_live"))

    def test_data_add_user_historical_protocol_registers_queryable_dataset(self) -> None:
        _require_optional_module(self, "pyarrow", "pyarrow optional dependency is not installed")
        with TemporaryDirectory() as temporary:
            connector = Path(temporary) / "my_signal_protocol.py"
            connector.write_text(
                "\n".join([
                    "def load(request):",
                    "    assert request.dataset_id == 'research.protocol_signal'",
                    "    assert request.start.isoformat() == '2026-01-01T00:00:00+00:00'",
                    "    assert request.instruments == ('AAPL',)",
                    "    return [",
                    "        {'timestamp': request.start.isoformat(), 'symbol': 'AAPL', 'value': 2.5},",
                    "    ]",
                    "",
                ]),
                encoding="utf-8",
            )

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "add", str(connector),
                    "--name", "research.protocol_signal",
                    "--protocol", "historical",
                    "--start", "2026-01-01T00:00:00+00:00",
                    "--instrument", "AAPL",
                ]), 0)
                payload = json.loads(output.getvalue())

            self.assertEqual(payload["dataset"], "research.protocol_signal")
            self.assertNotIn("source_kind", payload)
            self.assertEqual(payload["time"], "timestamp")
            self.assertEqual(payload["historical"]["status"], "ready_for_workspace")

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "query", "research.protocol_signal", "--limit", "1",
                ]), 0)
                query = json.loads(output.getvalue())
            self.assertEqual(query["dataset"], "research.protocol_signal")
            self.assertEqual(query["rows"][0]["symbol"], "AAPL")
            self.assertEqual(query["rows"][0]["value"], 2.5)

    def test_data_connect_user_live_protocol_creates_live_view(self) -> None:
        with TemporaryDirectory() as temporary:
            connector = Path(temporary) / "my_live_protocol.py"
            connector.write_text(
                "\n".join([
                    "async def stream(request):",
                    "    if False:",
                    "        yield {}",
                    "",
                ]),
                encoding="utf-8",
            )

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "connect", str(connector),
                    "--as", "research.live_signal",
                    "--time", "event_time",
                    "--account", "paper-feed",
                    "--channel", "quotes",
                    "--instrument", "AAPL",
                ]), 0)
                payload = json.loads(output.getvalue())

            self.assertEqual(payload["dataset"], "research.live_signal")
            self.assertNotIn("source_kind", payload)
            self.assertEqual(payload["time"], "event_time")
            self.assertEqual(payload["live"]["status"], "configured")
            self.assertEqual(payload["live"]["ready_for"], ["shadow"])
            self.assertNotIn("artifact_ref", payload)
            self.assertNotIn("live_view_id", payload)
            self.assertNotIn("manifest_hash", payload)
            self.assertTrue(_live_manifest_path(temporary, payload).exists())
            manifest = load_live_view_manifest(_live_manifest_path(temporary, payload))
            self.assertEqual(manifest.source["source_kind"], "user_defined")

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "doctor", "--dataset", "research.live_signal",
                ]), 0)
                doctor = json.loads(output.getvalue())
            api_doctor = Data(temporary).doctor("research.live_signal")
            self.assertEqual(doctor["status"], "needs_fix")
            self.assertEqual(doctor["live"]["ready_for"], ["shadow"])
            self.assertEqual(doctor["live"]["blocked_for"], ["paper", "live"])
            self.assertEqual(doctor["live"]["issues"], ["freshness_not_verified"])
            self.assertNotIn("source_kind", doctor)
            self.assertNotIn("live_view_id", json.dumps(doctor))
            self.assertEqual(api_doctor["dataset"], "research.live_signal")
            self.assertEqual(api_doctor["status"], doctor["status"])
            sdk_description = DatasetClient(temporary).describe("research.live_signal")
            self.assertEqual(sdk_description["dataset"], "research.live_signal")
            self.assertEqual(sdk_description["status"], "needs_fix")
            self.assertEqual(sdk_description["live"]["ready_for"], ["shadow"])

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "metadata", "research.live_signal",
                ]), 0)
                metadata = json.loads(output.getvalue())
            self.assertEqual(metadata["live"]["views"][0]["freshness_policy"]["max_age_seconds"], 5.0)
            self.assertEqual(metadata["live"]["views"][0]["freshness_policy"]["status"], "configured")
            self.assertNotIn("live_view_id", json.dumps(metadata))
            self.assertNotIn("manifest_hash", json.dumps(metadata))

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "audit", "research.live_signal",
                ]), 0)
                audit = json.loads(output.getvalue())
            self.assertEqual(audit["live"]["live_view_count"], 1)
            self.assertEqual(len(audit["live"]["live_views"][0]["manifest_hash"]), 64)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "reconnect", "research.live_signal",
                ]), 0)
                reconnected = json.loads(output.getvalue())
            self.assertEqual(reconnected["operation"], "reconnect")
            self.assertEqual(reconnected["dataset"], "research.live_signal")
            self.assertNotIn("source_kind", reconnected)
            self.assertNotIn("source_kind", reconnected["reused_configuration"])
            self.assertNotIn("source", reconnected["reused_configuration"])
            self.assertNotIn(str(connector), json.dumps(reconnected))
            self.assertNotIn("artifact_ref", json.dumps(reconnected))
            self.assertNotIn("live_view_id", json.dumps(reconnected))

    def test_data_doctor_blocks_paper_live_when_live_channel_gate_fails(self) -> None:
        with TemporaryDirectory() as temporary:
            connector = Path(temporary) / "my_live_protocol.py"
            connector.write_text(
                "\n".join([
                    "async def stream(request):",
                    "    if False:",
                    "        yield {}",
                    "",
                ]),
                encoding="utf-8",
            )
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "connect", str(connector),
                    "--as", "research.live_signal",
                    "--time", "event_time",
                    "--account", "paper-feed",
                    "--channel", "quotes",
                    "--instrument", "AAPL",
                ]), 0)
                payload = json.loads(output.getvalue())
            update_live_view_manifest_freshness(_live_manifest_path(temporary, payload), {
                "passed": True,
                "event_count": 10,
                "channel_dropped": 1,
                "channel_overflow": 2,
                "sequence_gaps": 0,
            })

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "doctor", "research.live_signal",
                ]), 0)
                doctor = json.loads(output.getvalue())

            self.assertEqual(doctor["status"], "needs_fix")
            self.assertEqual(doctor["live"]["ready_for"], ["shadow"])
            self.assertEqual(doctor["live"]["blocked_for"], ["paper", "live"])
            self.assertEqual(doctor["live"]["dropped"], 1)
            self.assertEqual(doctor["live"]["overflow"], 2)
            self.assertEqual(doctor["live"]["capture"], "enabled")
            self.assertEqual(doctor["live"]["issues"], ["channel_dropped", "channel_overflow"])
            self.assertNotIn("live_view_id", json.dumps(doctor))

    def test_data_doctor_marks_healthy_live_view_ready_for_paper(self) -> None:
        with TemporaryDirectory() as temporary:
            connector = Path(temporary) / "my_live_protocol.py"
            connector.write_text(
                "\n".join([
                    "async def stream(request):",
                    "    if False:",
                    "        yield {}",
                    "",
                ]),
                encoding="utf-8",
            )
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "connect", str(connector),
                    "--as", "research.live_signal",
                    "--time", "event_time",
                    "--account", "paper-feed",
                    "--channel", "quotes",
                    "--instrument", "AAPL",
                ]), 0)
                payload = json.loads(output.getvalue())
            update_live_view_manifest_freshness(_live_manifest_path(temporary, payload), {
                "passed": True,
                "event_count": 10,
                "channel_dropped": 0,
                "channel_overflow": 0,
                "sequence_gaps": 0,
            })

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "doctor", "research.live_signal",
                ]), 0)
                doctor = json.loads(output.getvalue())

            self.assertEqual(doctor["status"], "ready_for_paper")
            self.assertEqual(doctor["live"]["ready_for"], ["shadow", "paper"])
            self.assertEqual(doctor["live"]["blocked_for"], ["live"])
            self.assertEqual(doctor["live"]["freshness_status"], "healthy")
            self.assertEqual(doctor["live"]["dropped"], 0)
            self.assertEqual(doctor["live"]["overflow"], 0)
            self.assertEqual(doctor["live"]["capture"], "enabled")
            self.assertNotIn("live_view_id", json.dumps(doctor))

    def test_data_replay_live_dataset_uses_capture_evidence_without_user_path(self) -> None:
        with TemporaryDirectory() as temporary:
            connector = Path(temporary) / "my_live_protocol.py"
            connector.write_text(
                "\n".join([
                    "async def stream(request):",
                    "    if False:",
                    "        yield {}",
                    "",
                ]),
                encoding="utf-8",
            )
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "connect", str(connector),
                    "--as", "research.live_signal",
                    "--time", "event_time",
                    "--instrument", "AAPL",
                ]), 0)
                payload = json.loads(output.getvalue())

            capture = Path(temporary) / "captures" / "live_signal.canonical.jsonl"
            writer = CanonicalCaptureWriter(capture, session_id="test-live-signal", source="test")
            now = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)
            writer.append(CanonicalEventEnvelope(
                UUID("00000000-0000-0000-0000-000000000001"),
                "market.quote.v1",
                1,
                MarketEventKind.QUOTE,
                InstrumentId("equity:us:AAPL"),
                QuotePayload(Decimal("10"), Decimal("10.5"), Decimal("100"), Decimal("120")),
                "test",
                "test-live-signal",
                "aapl@bookTicker",
                "equity:us:AAPL",
                now,
                now,
                now,
                now,
                canonical_sequence=1,
            ))
            manifest = writer.finalize()
            update_live_view_manifest_freshness(_live_manifest_path(temporary, payload), {
                "passed": True,
                "event_count": 1,
                "channel_dropped": 0,
                "channel_overflow": 0,
                "sequence_gaps": 0,
                "artifact": manifest.event_path,
                "audit_hash": manifest.content_sha256,
            })

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "replay", "research.live_signal",
                ]), 0)
                replay = json.loads(output.getvalue())

        self.assertEqual(replay["operation"], "replay")
        self.assertEqual(replay["dataset"], "research.live_signal")
        self.assertEqual(replay["replay"]["source"], "live_capture")
        self.assertTrue(replay["replay"]["deterministic"])
        self.assertEqual(replay["rows"][0]["kind"], "quote")
        self.assertEqual(replay["rows"][0]["instrument_id"], {"value": "equity:us:AAPL"})
        self.assertNotIn("live_view_id", json.dumps(replay))
        self.assertNotIn("artifact", json.dumps(replay))
        self.assertNotIn(str(capture), json.dumps(replay))
        self.assertNotIn("audit_hash", json.dumps(replay))

    def test_data_replay_text_prints_live_capture_rows(self) -> None:
        with TemporaryDirectory() as temporary:
            connector = Path(temporary) / "my_live_protocol.py"
            connector.write_text(
                "\n".join([
                    "async def stream(request):",
                    "    if False:",
                    "        yield {}",
                    "",
                ]),
                encoding="utf-8",
            )
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary,
                    "data", "connect", str(connector),
                    "--as", "research.live_signal",
                    "--time", "event_time",
                    "--instrument", "AAPL",
                ]), 0)
                payload_text = output.getvalue()

            payload = {"dataset": "research.live_signal"}
            self.assertNotIn("live_view_id", payload_text)
            capture = Path(temporary) / "captures" / "live_signal.canonical.jsonl"
            writer = CanonicalCaptureWriter(capture, session_id="test-live-signal", source="test")
            now = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)
            writer.append(CanonicalEventEnvelope(
                UUID("00000000-0000-0000-0000-000000000001"),
                "market.quote.v1",
                1,
                MarketEventKind.QUOTE,
                InstrumentId("equity:us:AAPL"),
                QuotePayload(Decimal("10"), Decimal("10.5"), Decimal("100"), Decimal("120")),
                "test",
                "test-live-signal",
                "aapl@bookTicker",
                "equity:us:AAPL",
                now,
                now,
                now,
                now,
                canonical_sequence=1,
            ))
            manifest = writer.finalize()
            update_live_view_manifest_freshness(_live_manifest_path(temporary, payload), {
                "passed": True,
                "event_count": 1,
                "channel_dropped": 0,
                "channel_overflow": 0,
                "sequence_gaps": 0,
                "artifact": manifest.event_path,
                "audit_hash": manifest.content_sha256,
            })

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary,
                    "data", "replay", "research.live_signal",
                ]), 0)
                replay_text = output.getvalue()

        self.assertIn("Rows", replay_text)
        self.assertIn("quote", replay_text)
        self.assertIn("equity:us:AAPL", replay_text)
        row_lines = [
            line for line in replay_text.splitlines()
            if line.startswith("{") and line.endswith("}")
        ]
        self.assertEqual(len(row_lines), 1)
        rendered_row = json.loads(row_lines[0])
        self.assertEqual(rendered_row["kind"], "quote")
        self.assertEqual(rendered_row["instrument_id"]["value"], "equity:us:AAPL")
        self.assertNotIn("live_view_id", replay_text)
        self.assertNotIn(str(capture), replay_text)
        self.assertNotIn("audit_hash", replay_text)

    def test_data_replay_live_dataset_without_capture_reports_needs_data(self) -> None:
        with TemporaryDirectory() as temporary:
            connector = Path(temporary) / "my_live_protocol.py"
            connector.write_text(
                "\n".join([
                    "async def stream(request):",
                    "    if False:",
                    "        yield {}",
                    "",
                ]),
                encoding="utf-8",
            )
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "connect", str(connector),
                    "--as", "research.live_signal",
                    "--time", "event_time",
                    "--instrument", "AAPL",
                ]), 0)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "replay", "research.live_signal",
                ]), 2)
                replay = json.loads(output.getvalue())

        self.assertEqual(replay["status"], "needs_data")
        self.assertEqual(replay["issues"][0]["code"], "live_capture_not_available")
        self.assertIn("data connect", replay["next_command"])
        self.assertNotIn("live_view_id", json.dumps(replay))

    def test_data_replay_text_reports_live_capture_error(self) -> None:
        with TemporaryDirectory() as temporary:
            connector = Path(temporary) / "my_live_protocol.py"
            connector.write_text(
                "\n".join([
                    "async def stream(request):",
                    "    if False:",
                    "        yield {}",
                    "",
                ]),
                encoding="utf-8",
            )
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary,
                    "data", "connect", str(connector),
                    "--as", "research.live_signal",
                    "--time", "event_time",
                    "--instrument", "AAPL",
                ]), 0)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary,
                    "data", "replay", "research.live_signal",
                ]), 2)
                replay_text = output.getvalue()

        self.assertIn("Status", replay_text)
        self.assertIn("needs_data", replay_text)
        self.assertIn("live_capture_not_available", replay_text)
        self.assertIn("kairospy data connect", replay_text)
        self.assertNotIn("Returned Rows", replay_text)

    def test_data_connect_builtin_live_source_creates_live_view(self) -> None:
        with TemporaryDirectory() as temporary:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "connect", "binance.quote",
                    "--as", "market.quote.crypto.binance.btc-usdt",
                    "--account", "binance-testnet",
                    "--channel", "quote",
                    "--instrument", "BTCUSDT",
                    "--for", "paper",
                ]), 0)
                payload = json.loads(output.getvalue())

            self.assertEqual(payload["dataset"], "market.quote.crypto.binance.btc-usdt")
            self.assertEqual(payload["target_use"], "paper")
            self.assertNotIn("source_kind", payload)
            self.assertEqual(payload["provider"], "binance")
            self.assertEqual(payload["time"], "event_time")
            self.assertEqual(payload["live"]["ready_for"], ["shadow"])
            self.assertNotIn("artifact_ref", payload)
            self.assertNotIn("protocol_name", payload)
            self.assertNotIn("live_view_id", payload)
            self.assertEqual(payload["runtime"]["stream"], "btcusdt@bookTicker")
            self.assertEqual(payload["runtime"]["instrument_id"], "crypto:binance:spot:BTCUSDT")
            manifest = load_live_view_manifest(_live_manifest_path(temporary, payload))
            self.assertEqual(manifest.source["source_kind"], "built_in")
            self.assertEqual(manifest.live_data_plane["protocol_name"], "built_in.live.binance.quote")
            self.assertEqual(manifest.live_data_plane["target_use"], "paper")
            self.assertEqual(manifest.live_data_plane["stream"], "btcusdt@bookTicker")
            self.assertEqual(manifest.live_data_plane["symbol"], "BTCUSDT")
            self.assertEqual(manifest.live_data_plane["instrument_id"], "crypto:binance:spot:BTCUSDT")
            self.assertEqual(manifest.source["stream"], "btcusdt@bookTicker")
            self.assertEqual(
                manifest.contract_hash,
                stable_artifact_hash({
                    "dataset_id": "market.quote.crypto.binance.btc-usdt",
                    "primary_time": "event_time",
                    "fields": ["event_time"],
                    "source_kind": "built_in",
                }),
            )

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "doctor", "--dataset", "market.quote.crypto.binance.btc-usdt",
                ]), 0)
                doctor = json.loads(output.getvalue())
            self.assertEqual(doctor["status"], "needs_fix")
            self.assertEqual(doctor["live"]["issues"], ["freshness_not_verified"])
            self.assertNotIn("source_kind", doctor)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "reconnect", "market.quote.crypto.binance.btc-usdt",
                ]), 0)
                reconnected = json.loads(output.getvalue())
            self.assertEqual(reconnected["operation"], "reconnect")
            self.assertEqual(reconnected["target_use"], "paper")
            self.assertNotIn("source_kind", reconnected)
            self.assertEqual(reconnected["reused_configuration"]["source"], "binance.quote")
            self.assertNotIn("protocol_name", reconnected)
            self.assertNotIn("artifact_ref", json.dumps(reconnected))
            self.assertNotIn("live_view_id", json.dumps(reconnected))

    def test_data_connect_builtin_orderbook_source_uses_depth_stream(self) -> None:
        with TemporaryDirectory() as temporary:
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "connect", "binance.orderbook",
                    "--as", "market.orderbook.crypto.binance.btc-usdt",
                    "--account", "binance-testnet",
                    "--instrument", "BTCUSDT",
                ]), 0)
                payload = json.loads(output.getvalue())

            manifest = load_live_view_manifest(_live_manifest_path(temporary, payload))
            self.assertEqual(payload["dataset"], "market.orderbook.crypto.binance.btc-usdt")
            self.assertNotIn("artifact_ref", payload)
            self.assertNotIn("live_view_id", payload)
            self.assertNotIn("protocol_name", payload)
            self.assertEqual(payload["runtime"]["stream"], "btcusdt@depth")
            self.assertEqual(payload["runtime"]["channel"], "depth")
            self.assertEqual(manifest.live_data_plane["stream"], "btcusdt@depth")
            self.assertEqual(manifest.source["stream"], "btcusdt@depth")

    def test_data_connect_builtin_live_source_requires_instrument(self) -> None:
        with TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "requires exactly one --instrument"):
                main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "connect", "binance.quote",
                    "--as", "market.quote.crypto.binance.btc-usdt",
                    "--channel", "quote",
                ])

    def test_data_add_csv_infers_time_and_registers_queryable_dataset(self) -> None:
        _require_optional_module(self, "pyarrow", "pyarrow optional dependency is not installed")
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "signals.csv"
            source.write_text("date,symbol,value\n2026-01-01,AAPL,1.2\n", encoding="utf-8")

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "add", str(source), "--name", "research.my_signal",
                ]), 0)
                payload = json.loads(output.getvalue())

            self.assertEqual(payload["dataset"], "research.my_signal")
            self.assertEqual(payload["time"], "date")
            self.assertEqual(payload["historical"]["status"], "ready_for_workspace")
            self.assertNotIn("source_kind", payload)
            self.assertNotIn("release_id", payload)
            self.assertNotIn("manifest_hash", payload)
            self.assertNotIn("quality_report", payload)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "doctor", "research.my_signal",
                ]), 0)
                doctor = json.loads(output.getvalue())
            self.assertEqual(doctor["status"], "ready_for_workspace")
            self.assertEqual(doctor["historical"]["ready_for"], ["workspace"])
            self.assertNotIn("release_id", json.dumps(doctor))
            self.assertNotIn("selected_release", json.dumps(doctor))
            self.assertNotIn("live_view_id", json.dumps(doctor))
            self.assertNotIn("manifest_hash", json.dumps(doctor))

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary,
                    "data", "doctor", "research.my_signal",
                ]), 0)
                rendered_doctor = output.getvalue()
            self.assertIn("Dataset", rendered_doctor)
            self.assertIn("research.my_signal", rendered_doctor)
            self.assertIn("Ready For", rendered_doctor)
            self.assertIn("Blocked For", rendered_doctor)
            self.assertNotIn("healthy", rendered_doctor)
            self.assertNotIn("release", rendered_doctor.lower())

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary,
                    "data", "describe", "research.my_signal",
                ]), 0)
                rendered_description = output.getvalue()
            self.assertIn("Dataset", rendered_description)
            self.assertIn("Ready For", rendered_description)
            self.assertIn("Blocked For", rendered_description)
            self.assertNotIn("Source Kind", rendered_description)
            self.assertNotIn("{'status'", rendered_description)
            self.assertNotIn("release", rendered_description.lower())

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "describe", "research.my_signal",
                ]), 0)
                description = json.loads(output.getvalue())
            self.assertEqual(description["dataset"], "research.my_signal")
            self.assertEqual(description["status"], "ready_for_workspace")
            self.assertNotIn("manifest_hash", description)
            self.assertNotIn("content_hash", description)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "metadata", "research.my_signal",
                ]), 0)
                metadata = json.loads(output.getvalue())
            self.assertEqual(metadata["dataset"], "research.my_signal")
            self.assertEqual(metadata["historical"]["schema"]["primary_time"], "date")
            self.assertEqual(metadata["historical"]["schema"]["fields"], ["date", "symbol", "value"])
            self.assertEqual(metadata["historical"]["quality"]["row_count"], 1)
            self.assertNotIn("release_id", json.dumps(metadata))
            self.assertNotIn("content_hash", json.dumps(metadata))
            self.assertNotIn("manifest_hash", json.dumps(metadata))

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "validate", "research.my_signal",
                ]), 0)
                validation = json.loads(output.getvalue())
            api_validation = Data(temporary).validate("research.my_signal")
            self.assertEqual(validation["dataset"], "research.my_signal")
            self.assertEqual(validation["status"], "passed")
            self.assertEqual(validation["ready_for"], ["workspace"])
            self.assertEqual(api_validation["status"], validation["status"])
            self.assertTrue(any(item["name"] == "non_empty" for item in validation["checks"]))
            self.assertNotIn("release_id", json.dumps(validation))
            self.assertNotIn("content_hash", json.dumps(validation))
            self.assertNotIn("manifest_hash", json.dumps(validation))

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "audit", "research.my_signal",
                ]), 0)
                audit = json.loads(output.getvalue())
            release_audit = audit["historical"]["releases"][0]
            self.assertEqual(audit["dataset"], "research.my_signal")
            self.assertEqual(len(release_audit["manifest_hash"]), 64)
            self.assertEqual(len(release_audit["content_hash"]), 64)
            self.assertNotIn("lineage_summary", release_audit)
            self.assertNotIn("source_cache_summary", release_audit)
            self.assertNotIn("quality_report", release_audit)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "audit", "research.my_signal", "--verbose",
                ]), 0)
                verbose_audit = json.loads(output.getvalue())
            verbose_release = verbose_audit["historical"]["releases"][0]
            self.assertIn("documents", verbose_release)
            self.assertIn("lineage_summary", verbose_release)
            self.assertEqual(verbose_release["lineage_summary"]["source"]["name"], "signals.csv")
            self.assertIn("source_cache_summary", verbose_release)
            self.assertIn("signals.csv", verbose_release["source_cache_summary"]["stored_files"])
            self.assertIn(
                "event_year=all/event_month=all/part-000.csv",
                verbose_release["source_cache_summary"]["stored_files"],
            )
            cache_files = {
                item["relative_path"]: item
                for item in verbose_release["source_cache_summary"]["files"]
            }
            self.assertEqual(cache_files["signals.csv"]["name"], "signals.csv")
            self.assertEqual(len(cache_files["signals.csv"]["content_hash"]), 64)
            self.assertGreater(cache_files["signals.csv"]["size_bytes"], 0)
            self.assertIn("quality_report", verbose_release)
            self.assertEqual(verbose_release["quality_report"]["row_count"], 1)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "query", "research.my_signal", "--limit", "1",
                ]), 0)
                query = json.loads(output.getvalue())
            self.assertEqual(query["dataset"], "research.my_signal")
            self.assertEqual(query["rows"][0]["symbol"], "AAPL")
            self.assertEqual(query["rows"][0]["value"], 1.2)
            self.assertEqual(query["rows"][0]["date"], {"$date": "2026-01-01"})
            self.assertNotIn("release_id", json.dumps(query))
            self.assertNotIn("content_hash", json.dumps(query))
            self.assertNotIn("manifest_hash", json.dumps(query))

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "replay", "research.my_signal", "--limit", "1",
                ]), 0)
                replay = json.loads(output.getvalue())
            self.assertEqual(replay["dataset"], "research.my_signal")
            self.assertEqual(replay["time"], "date")
            self.assertEqual(replay["replay"]["source"], "governed_dataset")
            self.assertTrue(replay["replay"]["deterministic"])
            self.assertEqual(replay["rows"][0]["symbol"], "AAPL")
            self.assertNotIn("release_id", json.dumps(replay))
            self.assertNotIn("content_hash", json.dumps(replay))
            self.assertNotIn("manifest_hash", json.dumps(replay))
            self.assertNotIn(str(source), json.dumps(replay))

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "promote", "research.my_signal", "--for", "backtest",
                ]), 2)
                promotion = json.loads(output.getvalue())
            self.assertEqual(promotion["status"], "needs_fix")
            self.assertEqual(promotion["blocked_for"], ["backtest"])
            self.assertNotIn("release_id", promotion)

    def test_source_cache_store_copies_user_file_as_internal_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "signals.csv"
            source.write_text("date,symbol,value\n2026-01-01,AAPL,1.2\n", encoding="utf-8")
            store = SourceCacheStore(root)

            entry = store.cache_user_file(
                source,
                dataset_id="research.my_signal",
                release_id="research.my_signal:write:test",
            )
            summary = store.summary(
                store.release_directory("research.my_signal", "research.my_signal:write:test"),
                source={"kind": "file", "name": "signals.csv"},
            )

            self.assertEqual(entry.relative_path, "signals.csv")
            self.assertEqual(len(entry.content_hash), 64)
            self.assertEqual(summary["source"]["name"], "signals.csv")
            self.assertEqual(summary["stored_files"], ["signals.csv"])
            self.assertEqual(summary["files"][0]["relative_path"], "signals.csv")

    def test_data_metadata_can_override_historical_primary_time_without_contract_concepts(self) -> None:
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "signals.csv"
            source.write_text(
                "date,timestamp,symbol,value\n2026-01-01,2026-01-01T09:30:00Z,AAPL,1.2\n",
                encoding="utf-8",
            )
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "add", str(source), "--name", "research.my_signal",
                ]), 0)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "metadata", "research.my_signal", "--time", "timestamp",
                ]), 0)
                updated = json.loads(output.getvalue())

            self.assertEqual(updated["status"], "updated")
            self.assertEqual(updated["time"], "timestamp")
            self.assertEqual(updated["historical"]["schema"]["primary_time"], "timestamp")
            self.assertEqual(updated["updated"]["time"], "timestamp")
            self.assertNotIn("contract", json.dumps(updated).lower())
            self.assertNotIn("manifest_hash", json.dumps(updated))

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "metadata", "research.my_signal", "--time", "missing_time",
                ]), 2)
                failed = json.loads(output.getvalue())

            self.assertEqual(failed["status"], "needs_input")
            self.assertEqual(failed["issues"][0]["code"], "time_field_not_found")

    def test_data_add_is_idempotent_for_same_dataset_and_file(self) -> None:
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "signals.csv"
            source.write_text("date,symbol,value\n2026-01-01,AAPL,1.2\n", encoding="utf-8")

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "add", str(source), "--name", "research.my_signal",
                ]), 0)
                first = json.loads(output.getvalue())
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "add", str(source), "--name", "research.my_signal",
                ]), 0)
                second = json.loads(output.getvalue())

            catalog = DataCatalog(temporary)
            releases = catalog.releases(catalog.product("research.my_signal"))
            self.assertNotIn("artifact_ref", first)
            self.assertNotIn("artifact_ref", second)
            self.assertNotIn("release_id", first)
            self.assertNotIn("release_id", second)
            self.assertEqual(len(releases), 1)

    def test_data_add_parquet_infers_time_and_registers_queryable_dataset(self) -> None:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            self.skipTest("pyarrow is required for parquet data add")

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "signals.parquet"
            pq.write_table(
                pa.table({
                    "event_time": ["2026-01-01T00:00:00+00:00"],
                    "symbol": ["AAPL"],
                    "value": [1.2],
                }),
                source,
            )

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "add", str(source), "--name", "research.parquet_signal",
                ]), 0)
                payload = json.loads(output.getvalue())

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "query", "--dataset", "research.parquet_signal", "--limit", "1",
                ]), 0)
                query = json.loads(output.getvalue())

        self.assertEqual(payload["dataset"], "research.parquet_signal")
        self.assertEqual(payload["time"], "event_time")
        self.assertEqual(payload["historical"]["status"], "ready_for_workspace")
        self.assertEqual(query["rows"][0]["symbol"], "AAPL")
        self.assertEqual(query["rows"][0]["value"], 1.2)

    def test_data_user_code_round_trip_uses_only_dataset_names_and_user_files(self) -> None:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            self.skipTest("pyarrow is required for parquet data add")

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "signals.csv"
            source.write_text("date,symbol,value\n2026-01-01,AAPL,1.2\n", encoding="utf-8")

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "add", str(source), "--name", "tutorial.signal",
                ]), 0)
                added = json.loads(output.getvalue())
            self.assertEqual(added["historical"]["status"], "ready_for_workspace")

            rows = DatasetClient(temporary).load_rows("tutorial.signal")
            output_path = root / "outputs" / "signal.parquet"
            output_path.parent.mkdir()
            pq.write_table(
                pa.table({
                    "date": [rows[0]["date"]],
                    "symbol": [rows[0]["symbol"]],
                    "signal_value": [float(rows[0]["value"]) * 2],
                }),
                output_path,
            )

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "add", str(output_path), "--name", "tutorial.signal_output",
                ]), 0)
                output_added = json.loads(output.getvalue())
            self.assertEqual(output_added["time"], "date")
            self.assertEqual(output_added["historical"]["status"], "ready_for_workspace")

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "doctor", "tutorial.signal_output",
                ]), 0)
                doctor = json.loads(output.getvalue())
            self.assertEqual(doctor["status"], "ready_for_workspace")
            self.assertNotIn("release_id", doctor)
            self.assertNotIn("manifest_hash", doctor)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "audit", "tutorial.signal_output", "--verbose",
                ]), 0)
                audit = json.loads(output.getvalue())
            self.assertIn("documents", audit["historical"]["releases"][0])

    def test_data_promote_dataset_for_backtest_updates_doctor_readiness(self) -> None:
        _require_optional_module(self, "pyarrow", "pyarrow optional dependency is not installed")
        _require_optional_module(self, "duckdb", "query optional dependency is not installed")
        with TemporaryDirectory() as temporary:
            dataset = "market.ohlcv.crypto.test.btc-usdt.1d"
            rows = []
            start = date(2025, 1, 1)
            for index in range(365):
                day = start + timedelta(days=index)
                next_day = day + timedelta(days=1)
                rows.append({
                    "instrument_id": "BTC-USDT",
                    "period_start": utc_midnight(day),
                    "period_end": utc_midnight(next_day),
                    "event_time": utc_midnight(next_day),
                    "available_time": utc_midnight(next_day),
                    "open": 100,
                    "high": 110,
                    "low": 90,
                    "close": 105,
                    "volume": 1,
                })
            product = DataProductDefinition(
                DatasetKey(dataset),
                "BTC/USDT daily OHLCV test",
                DatasetLayer.CANONICAL,
                "Backtest-ready OHLCV fixture",
                {"asset_class": "crypto", "frequency": "1d"},
                "period_start",
                owner="test",
            )
            relative_path = "canonical/test/backtest-ready"
            manifest = write_daily_dataset(
                Path(temporary) / relative_path,
                rows,
                dataset_id=dataset,
                schema={"schema_id": "market.ohlcv.v1", "primary_key": ["period_start"]},
                lineage={"source": {"provider": "test"}},
            )
            catalog = DataCatalog(temporary)
            catalog.register_product_spec(DataProductContract(
                product,
                relative_path,
                "market.ohlcv.v1",
                quality_profile="ohlcv",
            ))
            catalog.register_release(DatasetRelease(
                "release-backtest-ready",
                product.key,
                "1",
                "market.ohlcv.v1",
                "1",
                "fixture",
                "1",
                relative_path,
                "parquet",
                str(manifest["dataset_sha256"]),
                "test",
                None,
                (),
                DatasetStatus.APPROVED_FOR_WORKSPACE,
                QualityLevel.WORKSPACE,
            ))
            catalog.save()

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "promote", dataset, "--for", "backtest",
                ]), 0)
                promotion = json.loads(output.getvalue())
            self.assertEqual(promotion["status"], "ready_for_backtest")
            self.assertEqual(promotion["ready_for"], ["workspace", "backtest"])

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "doctor", "--dataset", dataset,
                ]), 0)
                doctor = json.loads(output.getvalue())
            self.assertEqual(doctor["status"], "ready_for_backtest")
            self.assertIn("backtest", doctor["ready_for"])

    def test_data_add_requires_time_when_header_has_no_time_candidate(self) -> None:
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "signals.csv"
            source.write_text("trade_day,symbol,value\n2026-01-01,AAPL,1.2\n", encoding="utf-8")

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "add", str(source), "--name", "research.my_signal",
                ]), 2)
                missing_time = json.loads(output.getvalue())
            self.assertEqual(missing_time["status"], "needs_time")
            self.assertEqual(missing_time["dataset"], "research.my_signal")
            self.assertEqual(missing_time["detected_fields"], ["trade_day", "symbol", "value"])
            self.assertEqual(missing_time["required"], ["--time"])
            self.assertIn("--time trade_day", missing_time["example"])
            with self.assertRaisesRegex(ValueError, "no time field detected.*--time") as raised:
                Data(temporary).add(source, name="research.api_signal")
            self.assertEqual(getattr(raised.exception, "fields"), ("trade_day", "symbol", "value"))

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary, "--format", "json",
                    "data", "add", str(source), "--name", "research.my_signal", "--time", "trade_day",
                ]), 0)
                payload = json.loads(output.getvalue())
            self.assertEqual(payload["time"], "trade_day")


if __name__ == "__main__":
    unittest.main()
