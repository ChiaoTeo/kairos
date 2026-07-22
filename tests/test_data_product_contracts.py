from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

from kairospy.data.bootstrap import (
    configured_product_specs, default_provider_registry, register_configured_products,
    register_default_products,
)
from kairospy.data import AcquisitionRequest, DatasetClient, TimeRange
from kairospy.data.catalog import DataCatalog
from kairospy.data.contracts import (
    DataProductContract, DataReleaseManifest, DataSetContractArtifact, DatasetStorageKind, LiveViewManifest,
    QualityLevel,
)
from kairospy.data.freshness import (
    PAPER_LIVE_FRESHNESS_POLICY, evaluate_live_view_freshness, live_view_channel_diagnostics,
    find_live_view_manifest, live_view_freshness_evidence, live_view_manifest_path, update_live_view_manifest_freshness,
    resolve_live_view_subscription, write_live_view_manifest,
)
from kairospy.data.products import (
    BTC_SPOT_DAILY, US_EQUITY_LIQUIDITY_DAILY, US_EQUITY_MASSIVE_CORPORATE_ACTIONS,
    US_EQUITY_MASSIVE_IDENTITY,
    US_EQUITY_MASSIVE_RAW_DAILY, US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY, US_EQUITY_MOMENTUM_DAILY,
    US_EQUITY_RETURNS_DAILY, US_EQUITY_UNIVERSE_DAILY, US_OPTION_MASSIVE_RAW_HOURLY,
)


class DataProductContractTests(unittest.TestCase):
    def test_builtin_specs_are_the_catalog_and_provider_registry_contract(self) -> None:
        self.assertFalse((Path("kairospy") / "data" / "models.py").exists())
        self.assertIs(DataProductContract, type(BTC_SPOT_DAILY))
        with tempfile.TemporaryDirectory() as directory:
            catalog = register_default_products(directory)
            spec = catalog.product_spec(str(BTC_SPOT_DAILY.key))
            providers = default_provider_registry(directory)

            self.assertEqual(spec, BTC_SPOT_DAILY)
            self.assertEqual(spec.quality_profile, "ohlcv")
            self.assertEqual(spec.minimum_publication_level, QualityLevel.BACKTEST)
            self.assertEqual(providers.product_spec(str(spec.key)), spec)

            restored = DataCatalog(directory)
            self.assertEqual(restored.product_spec(str(spec.key)), spec)
            registry = json.loads((Path(directory) / "catalog" / "datasets.json").read_text())
            self.assertEqual(registry["schema_version"], 4)
            self.assertGreaterEqual(len(registry["product_specs"]), 8)

    def test_us_equity_momentum_products_are_registered_as_explicit_contracts(self) -> None:
        expected = (
            US_EQUITY_MASSIVE_RAW_DAILY,
            US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY,
            US_EQUITY_MASSIVE_CORPORATE_ACTIONS,
            US_EQUITY_MASSIVE_IDENTITY,
            US_OPTION_MASSIVE_RAW_HOURLY,
            US_EQUITY_RETURNS_DAILY,
            US_EQUITY_UNIVERSE_DAILY,
            US_EQUITY_LIQUIDITY_DAILY,
            US_EQUITY_MOMENTUM_DAILY,
        )
        with tempfile.TemporaryDirectory() as directory:
            catalog = register_default_products(directory)
            for spec in expected:
                restored = catalog.product_spec(str(spec.key))
                self.assertEqual(restored, spec)
                expected_product = "option" if str(restored.key) == str(US_OPTION_MASSIVE_RAW_HOURLY.key) else "equity"
                self.assertIn(expected_product, restored.capabilities["supported_products"])
                self.assertTrue(restored.relative_path)
            self.assertEqual(US_EQUITY_MASSIVE_RAW_DAILY.minimum_publication_level, QualityLevel.WORKSPACE)
            self.assertEqual(US_EQUITY_MASSIVE_CORPORATE_ACTIONS.quality_profile, "corporate_action")
            self.assertEqual(US_EQUITY_MASSIVE_CORPORATE_ACTIONS.storage_kind, DatasetStorageKind.REFERENCE)
            self.assertEqual(US_EQUITY_MASSIVE_IDENTITY.quality_profile, "equity_identity")
            self.assertEqual(US_EQUITY_MASSIVE_IDENTITY.storage_kind, DatasetStorageKind.REFERENCE)
            self.assertEqual(US_OPTION_MASSIVE_RAW_HOURLY.product.sources[0].venue, "opra")
            self.assertEqual(US_OPTION_MASSIVE_RAW_HOURLY.product.dimensions["universe"], "full-market-or-explicit-contracts")
            self.assertEqual(US_EQUITY_MOMENTUM_DAILY.minimum_publication_level, QualityLevel.BACKTEST)

    def test_dynamic_config_compiles_once_for_catalog_and_connector(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "connectors.json"
            config.write_text(json.dumps({"massive_option_products": [{
                "logical_key": "market.events.options.us.test",
                "title": "TEST option events",
                "underlying": "TEST",
                "option_tickers": ["O:TEST260130C00100000"],
                "dimensions": {"venue": "opra", "asset_class": "option"},
            }]}), encoding="utf-8")
            compiled = configured_product_specs(config)[0]
            catalog = register_configured_products(directory, config)
            providers = default_provider_registry(directory, data_product_config=config)

            self.assertEqual(catalog.product_spec(str(compiled.key)), compiled)
            self.assertEqual(providers.product_spec(str(compiled.key)), compiled)
            self.assertEqual(compiled.storage_kind, DatasetStorageKind.MARKET_EVENTS)
            self.assertEqual(compiled.quality_profile, "market_event")
            self.assertEqual(compiled.minimum_publication_level, QualityLevel.BACKTEST)
            self.assertEqual(compiled.product.owner, "data-platform")

    def test_massive_equity_config_compiles_as_bounded_daily_ohlcv_product(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "connectors.json"
            config.write_text(json.dumps({"massive_equity_products": [{
                "logical_key": "market.ohlcv.equity.us.massive.nvda.1d.raw",
                "title": "NVDA raw daily bars",
                "ticker": "NVDA",
                "view": "raw",
            }]}), encoding="utf-8")
            compiled = configured_product_specs(config)[0]
            catalog = register_configured_products(directory, config)
            providers = default_provider_registry(directory, data_product_config=config)

            self.assertEqual(catalog.product_spec(str(compiled.key)), compiled)
            self.assertEqual(providers.product_spec(str(compiled.key)), compiled)
            self.assertEqual(compiled.storage_kind, DatasetStorageKind.TABULAR)
            self.assertEqual(compiled.quality_profile, "equity_ohlcv")
            self.assertEqual(compiled.minimum_publication_level, QualityLevel.WORKSPACE)
            self.assertEqual(compiled.product.dimensions["view"], "raw")

    def test_provider_extension_registers_product_spec_and_builder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extension = root / "demo_provider.py"
            extension.write_text(
                "\n".join([
                    "from kairospy.data import (",
                    "    AcquisitionEstimate, DataProductContract, DataProductDefinition, DatasetKey,",
                    "    DatasetLayer, DatasetStorageKind, QualityLevel, SourceBinding,",
                    ")",
                    "KEY = 'market.demo.extension.ohlcv'",
                    "def products(context):",
                    "    product = DataProductDefinition(",
                    "        DatasetKey(KEY), 'Demo extension OHLCV', DatasetLayer.CANONICAL,",
                    "        'Demo provider extension.', {'provider': 'demo', 'asset_class': 'equity'},",
                    "        'period_start',",
                    "        sources=(SourceBinding('demo', 'test', 100, QualityLevel.WORKSPACE, ('python',)),),",
                    "    )",
                    "    return (DataProductContract(",
                    "        product, 'canonical/demo/extension', 'market.demo.ohlcv.v1',",
                    "        {'supported_products': ['equity']}, DatasetStorageKind.TABULAR,",
                    "        '1', 'demo_ohlcv', QualityLevel.WORKSPACE,",
                    "    ),)",
                    "class DemoBuilder:",
                    "    provider = 'demo'",
                    "    def supports(self, logical_key):",
                    "        return logical_key == KEY",
                    "    def estimate(self, request):",
                    "        return AcquisitionEstimate(1, cost_class='python-extension', instruments=len(request.instruments))",
                    "    def acquire(self, request):",
                    "        raise RuntimeError('not used in this test')",
                    "def register(registry, context):",
                    "    registry.register(DemoBuilder(), products(context))",
                    "",
                ]),
                encoding="utf-8",
            )
            config = root / "connectors.json"
            config.write_text(json.dumps({"provider_extensions": [{"path": extension.name}]}), encoding="utf-8")

            compiled = configured_product_specs(config)[0]
            catalog = register_configured_products(directory, config)
            providers = default_provider_registry(directory, data_product_config=config)
            request = AcquisitionRequest(
                str(compiled.key),
                (TimeRange(
                    datetime(2026, 1, 1, tzinfo=timezone.utc),
                    datetime(2026, 1, 2, tzinfo=timezone.utc),
                ),),
                compiled.product.sources[0],
                instruments=("AAPL",),
            )

            self.assertEqual(catalog.product_spec(str(compiled.key)), compiled)
            self.assertTrue(providers.available("demo", str(compiled.key)))
            self.assertEqual(providers.product_spec(str(compiled.key)), compiled)
            self.assertEqual(providers.get("demo", str(compiled.key)).estimate(request).cost_class, "python-extension")

    def test_external_process_extension_acquires_file_manifest_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider_script = root / "external_provider.py"
            provider_script.write_text(
                "\n".join([
                    "import json",
                    "from pathlib import Path",
                    "request = json.loads(__import__('sys').stdin.read())",
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
                    "    'request_product': request['product'],",
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
                        "dimensions": {"asset_class": "equity"},
                    }],
                }],
            }), encoding="utf-8")

            catalog = register_configured_products(directory, config)
            providers = default_provider_registry(directory, data_product_config=config)
            client = DatasetClient(directory, providers=providers)
            plan = client.plan(
                "market.demo.process.signal",
                start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                end=datetime(2026, 1, 2, tzinfo=timezone.utc),
                provider="demo-process",
                venue="test",
            )
            release = client.acquire(plan, instruments=("AAPL",))

            self.assertEqual(catalog.product_spec("market.demo.process.signal").product.sources[0].provider, "demo-process")
            self.assertTrue(providers.available("demo-process", "market.demo.process.signal"))
            self.assertEqual(release.provider, "demo-process")
            self.assertEqual(release.venue, "test")
            self.assertTrue((root / release.relative_path / "manifest.json").exists())
            self.assertTrue((root / release.relative_path / "release.json").exists())

    def test_product_spec_rejects_unsafe_physical_layout(self) -> None:
        with self.assertRaisesRegex(ValueError, "safe lake-relative"):
            replace(BTC_SPOT_DAILY, relative_path="../outside")

    def test_dataset_contract_artifact_excludes_physical_layout_from_hash(self) -> None:
        relocated = replace(BTC_SPOT_DAILY, relative_path="canonical/relocated/btc")

        original_artifact = DataSetContractArtifact.from_product_contract(BTC_SPOT_DAILY)
        relocated_artifact = DataSetContractArtifact.from_product_contract(relocated)
        primitive = original_artifact.to_primitive()

        self.assertEqual(original_artifact.contract_hash, relocated_artifact.contract_hash)
        self.assertNotIn("relative_path", json.dumps(primitive, sort_keys=True))
        self.assertEqual(primitive["dataset_id"], str(BTC_SPOT_DAILY.key))
        self.assertEqual(primitive["storage"]["kind"], DatasetStorageKind.TABULAR.value)

    def test_data_release_and_live_view_manifests_have_stable_refs_and_hashes(self) -> None:
        contract_hash = DataSetContractArtifact.from_product_contract(BTC_SPOT_DAILY).contract_hash
        release_manifest = DataReleaseManifest(
            str(BTC_SPOT_DAILY.key),
            "release:test",
            contract_hash,
            "content-hash",
            "available_time",
            ("available_time", "close"),
            QualityLevel.BACKTEST,
            {"kind": "fixture"},
            "2026-07-20T00:00:00+00:00",
        )
        live_manifest = LiveViewManifest(
            str(BTC_SPOT_DAILY.key),
            "live:test",
            contract_hash,
            "connector-hash",
            "available_time",
            ("available_time", "close"),
            {"channel_contract": "BoundedEventChannel"},
            {"kind": "live_connector"},
            "configured",
            "2026-07-20T00:00:00+00:00",
        )

        self.assertEqual(release_manifest.artifact_ref, f"data://{BTC_SPOT_DAILY.key}/releases/release:test")
        self.assertEqual(live_manifest.artifact_ref, f"data://{BTC_SPOT_DAILY.key}/live-views/live:test")
        self.assertEqual(len(release_manifest.manifest_hash), 64)
        self.assertEqual(release_manifest.manifest_hash, DataReleaseManifest(
            str(BTC_SPOT_DAILY.key),
            "release:test",
            contract_hash,
            "content-hash",
            "available_time",
            ("available_time", "close"),
            QualityLevel.BACKTEST,
            {"kind": "fixture"},
            "2026-07-20T00:00:00+00:00",
        ).manifest_hash)
        self.assertEqual(live_manifest.to_primitive()["kind"], "live_view_manifest")

    def test_live_view_freshness_gate_separates_configured_from_paper_live(self) -> None:
        contract_hash = DataSetContractArtifact.from_product_contract(BTC_SPOT_DAILY).contract_hash
        configured = LiveViewManifest(
            str(BTC_SPOT_DAILY.key),
            "live:configured",
            contract_hash,
            "connector-hash",
            "available_time",
            ("available_time", "close"),
            {"channel_contract": "BoundedEventChannel", "freshness": {"max_age_seconds": 60}},
            {"kind": "live_connector"},
            "configured",
            "2026-07-20T00:00:00+00:00",
        )
        configured_gate = evaluate_live_view_freshness(configured)
        paper_live_gate = evaluate_live_view_freshness(configured, policy=PAPER_LIVE_FRESHNESS_POLICY)

        self.assertTrue(configured_gate.passed)
        self.assertEqual(configured_gate.max_age_seconds, 60)
        self.assertFalse(paper_live_gate.passed)
        self.assertEqual(paper_live_gate.channel_failures, ("missing_channel_diagnostics",))

        healthy = LiveViewManifest(
            str(BTC_SPOT_DAILY.key),
            "live:healthy",
            contract_hash,
            "connector-hash",
            "available_time",
            ("available_time", "close"),
            {
                "channel_contract": "BoundedEventChannel",
                "freshness": {"max_age_seconds": 60},
                "channel_diagnostics": {"dropped": 0, "sequence_gaps": 0, "conflated": 1, "reconnects": 1},
            },
            {"kind": "live_connector"},
            "healthy",
            "2026-07-20T00:00:00+00:00",
        )

        self.assertTrue(evaluate_live_view_freshness(healthy, policy=PAPER_LIVE_FRESHNESS_POLICY).passed)

    def test_paper_live_freshness_gate_fails_on_missing_or_lossy_channel_diagnostics(self) -> None:
        contract_hash = DataSetContractArtifact.from_product_contract(BTC_SPOT_DAILY).contract_hash
        missing = LiveViewManifest(
            str(BTC_SPOT_DAILY.key),
            "live:missing-channel-diagnostics",
            contract_hash,
            "connector-hash",
            "available_time",
            ("available_time", "close"),
            {"channel_contract": "BoundedEventChannel", "freshness": {"max_age_seconds": 60}},
            {"kind": "live_connector"},
            "healthy",
            "2026-07-20T00:00:00+00:00",
        )
        missing_gate = evaluate_live_view_freshness(missing, policy=PAPER_LIVE_FRESHNESS_POLICY)

        self.assertFalse(missing_gate.passed)
        self.assertEqual(missing_gate.channel_failures, ("missing_channel_diagnostics",))

        lossy = LiveViewManifest(
            str(BTC_SPOT_DAILY.key),
            "live:lossy-channel",
            contract_hash,
            "connector-hash",
            "available_time",
            ("available_time", "close"),
            {
                "channel_contract": "BoundedEventChannel",
                "freshness": {"max_age_seconds": 60},
                "channel_diagnostics": {"dropped": 1, "channel_overflow": 1, "sequence_gaps": 1, "reconnects": 1},
            },
            {"kind": "live_connector"},
            "healthy",
            "2026-07-20T00:00:00+00:00",
        )
        lossy_gate = evaluate_live_view_freshness(lossy, policy=PAPER_LIVE_FRESHNESS_POLICY)

        self.assertFalse(lossy_gate.passed)
        self.assertIn("channel_dropped", lossy_gate.channel_failures)
        self.assertIn("channel_overflow", lossy_gate.channel_failures)
        self.assertIn("sequence_gap", lossy_gate.channel_failures)
        self.assertEqual(lossy_gate.channel_diagnostics["overflow"], 1)

    def test_live_view_channel_diagnostics_normalizes_soak_report_fields(self) -> None:
        diagnostics = live_view_channel_diagnostics({
            "channel_capacity": 64,
            "peak_channel_depth": 8,
            "peak_channel_utilization": 0.125,
            "channel_dropped": 0,
            "channel_overflow": 0,
            "reconnect_count": 2,
        })

        self.assertEqual(diagnostics["capacity"], 64)
        self.assertEqual(diagnostics["peak_depth"], 8)
        self.assertEqual(diagnostics["peak_utilization"], 0.125)
        self.assertEqual(diagnostics["dropped"], 0)
        self.assertEqual(diagnostics["overflow"], 0)
        self.assertEqual(diagnostics["sequence_gaps"], 0)
        self.assertEqual(diagnostics["reconnects"], 2)

    def test_live_view_freshness_evidence_reads_connector_service_metrics(self) -> None:
        class Metrics:
            capacity = 64
            peak_depth = 4
            dropped = 0

        class Channel:
            metrics = Metrics()

        class Service:
            raw_messages = 5
            canonical_events = 5
            ignored_messages = 0
            reconnects = 1
            canonical_capture = None

        evidence = live_view_freshness_evidence(
            Service(), Channel(), source="binance", stream_id="btcusdt@bookTicker",
        )

        self.assertTrue(evidence["passed"])
        self.assertEqual(evidence["event_count"], 5)
        self.assertEqual(evidence["channel_capacity"], 64)
        self.assertEqual(evidence["peak_channel_depth"], 4)
        self.assertEqual(evidence["reconnect_count"], 1)

    def test_live_view_freshness_evidence_fails_on_ignored_or_dropped_events(self) -> None:
        class Metrics:
            capacity = 64
            peak_depth = 64
            dropped = 1

        class Channel:
            metrics = Metrics()

        class Service:
            raw_messages = 5
            canonical_events = 4
            ignored_messages = 1
            reconnects = 0
            canonical_capture = None

        evidence = live_view_freshness_evidence(
            Service(), Channel(), source="binance", stream_id="btcusdt@bookTicker",
        )

        self.assertFalse(evidence["passed"])
        self.assertEqual(evidence["channel_dropped"], 1)
        self.assertEqual(evidence["sequence_gaps"], 1)

    def test_soak_evidence_updates_live_view_manifest_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            contract_hash = DataSetContractArtifact.from_product_contract(BTC_SPOT_DAILY).contract_hash
            manifest = LiveViewManifest(
                str(BTC_SPOT_DAILY.key),
                "live:soak-updated",
                contract_hash,
                "connector-hash",
                "available_time",
                ("available_time", "close"),
                {"channel_contract": "BoundedEventChannel", "freshness": {"max_age_seconds": 60}},
                {"kind": "live_connector"},
                "configured",
                "2026-07-20T00:00:00+00:00",
            )
            path.write_text(json.dumps(manifest.to_primitive()), encoding="utf-8")

            updated = update_live_view_manifest_freshness(path, {
                "passed": True,
                "artifact": str(Path(directory) / "soak.json"),
                "audit_hash": "a" * 64,
                "source": "binance",
                "stream_id": "btcusdt@bookTicker",
                "event_count": 10,
                "channel_capacity": 64,
                "peak_channel_depth": 2,
                "peak_channel_utilization": 0.03125,
                "channel_dropped": 0,
                "reconnect_count": 1,
            })
            payload = json.loads(path.read_text(encoding="utf-8"))
            gate = evaluate_live_view_freshness(updated, policy=PAPER_LIVE_FRESHNESS_POLICY)

        self.assertEqual(payload["freshness_status"], "healthy")
        self.assertEqual(payload["live_data_plane"]["channel_diagnostics"]["dropped"], 0)
        self.assertEqual(payload["live_data_plane"]["freshness_evidence"]["audit_hash"], "a" * 64)
        self.assertTrue(gate.passed)

    def test_soak_evidence_marks_live_view_unhealthy_when_channel_is_lossy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            contract_hash = DataSetContractArtifact.from_product_contract(BTC_SPOT_DAILY).contract_hash
            manifest = LiveViewManifest(
                str(BTC_SPOT_DAILY.key),
                "live:lossy-soak",
                contract_hash,
                "connector-hash",
                "available_time",
                ("available_time", "close"),
                {"channel_contract": "BoundedEventChannel", "freshness": {"max_age_seconds": 60}},
                {"kind": "live_connector"},
                "configured",
                "2026-07-20T00:00:00+00:00",
            )
            path.write_text(json.dumps(manifest.to_primitive()), encoding="utf-8")

            updated = update_live_view_manifest_freshness(path, {
                "passed": True,
                "audit_hash": "b" * 64,
                "event_count": 10,
                "channel_capacity": 64,
                "channel_dropped": 1,
                "sequence_gaps": 1,
            })
            payload = json.loads(path.read_text(encoding="utf-8"))
            gate = evaluate_live_view_freshness(updated, policy=PAPER_LIVE_FRESHNESS_POLICY)

        self.assertEqual(payload["freshness_status"], "unhealthy")
        self.assertIn("channel_dropped", payload["live_data_plane"]["freshness_evidence"]["channel_failures"])
        self.assertFalse(gate.passed)

    def test_find_live_view_manifest_prefers_policy_passing_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_id = str(BTC_SPOT_DAILY.key)
            contract_hash = DataSetContractArtifact.from_product_contract(BTC_SPOT_DAILY).contract_hash
            configured = LiveViewManifest(
                dataset_id,
                "live:configured",
                contract_hash,
                "configured-connector",
                "available_time",
                ("available_time", "close"),
                {"channel_contract": "BoundedEventChannel", "freshness": {"max_age_seconds": 60}},
                {"kind": "live_connector"},
                "configured",
                "2026-07-20T00:00:00+00:00",
            )
            healthy = LiveViewManifest(
                dataset_id,
                "live:healthy",
                contract_hash,
                "healthy-connector",
                "available_time",
                ("available_time", "close"),
                {
                    "channel_contract": "BoundedEventChannel",
                    "freshness": {"max_age_seconds": 60},
                    "channel_diagnostics": {"dropped": 0, "overflow": 0, "sequence_gaps": 0},
                },
                {"kind": "live_connector"},
                "healthy",
                "2026-07-20T00:00:00+00:00",
            )
            write_live_view_manifest(live_view_manifest_path(root, dataset_id, configured.live_view_id), configured)
            write_live_view_manifest(live_view_manifest_path(root, dataset_id, healthy.live_view_id), healthy)

            selected = find_live_view_manifest(
                root, dataset_id=dataset_id, contract_hash=contract_hash, policy=PAPER_LIVE_FRESHNESS_POLICY,
            )

        self.assertIsNotNone(selected)
        self.assertEqual(selected.live_view_id, "live:healthy")

    def test_resolve_live_view_subscription_returns_runtime_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_id = str(BTC_SPOT_DAILY.key)
            contract_hash = DataSetContractArtifact.from_product_contract(BTC_SPOT_DAILY).contract_hash
            manifest = LiveViewManifest(
                dataset_id,
                "live:subscription",
                contract_hash,
                "connector-hash",
                "available_time",
                ("available_time", "close"),
                {
                    "transport": "connector",
                    "event_source_contract": "EventSource[DataSetRecord]",
                    "channel_contract": "BoundedEventChannel",
                    "freshness": {"max_age_seconds": 60},
                    "channel_diagnostics": {"dropped": 0, "overflow": 0, "sequence_gaps": 0},
                },
                {"kind": "live_connector"},
                "healthy",
                "2026-07-20T00:00:00+00:00",
            )
            write_live_view_manifest(live_view_manifest_path(root, dataset_id, manifest.live_view_id), manifest)

            binding = resolve_live_view_subscription(
                root, name="bars", dataset_id=dataset_id, contract_hash=contract_hash,
            )
            primitive = binding.to_primitive()

        self.assertEqual(binding.live_view_id, "live:subscription")
        self.assertEqual(primitive["name"], "bars")
        self.assertEqual(primitive["event_source_contract"], "EventSource[DataSetRecord]")
        self.assertTrue(primitive["freshness_gate"]["passed"])


if __name__ == "__main__":
    unittest.main()
