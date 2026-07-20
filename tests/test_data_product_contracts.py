from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from kairos.data.bootstrap import (
    configured_product_specs, default_provider_registry, register_configured_products,
    register_default_products,
)
from kairos.data.catalog import DataCatalog
from kairos.data.contracts import (
    DataProductContract, DataReleaseManifest, DataSetContractArtifact, DatasetStorageKind, LiveViewManifest,
    QualityLevel,
)
from kairos.data.freshness import (
    PAPER_LIVE_FRESHNESS_POLICY, evaluate_live_view_freshness, live_view_channel_diagnostics,
    find_live_view_manifest, live_view_manifest_path, update_live_view_manifest_freshness,
    write_live_view_manifest,
)
from kairos.data.products import (
    BTC_SPOT_DAILY, US_EQUITY_LIQUIDITY_DAILY, US_EQUITY_MASSIVE_CORPORATE_ACTIONS,
    US_EQUITY_MASSIVE_IDENTITY,
    US_EQUITY_MASSIVE_RAW_DAILY, US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY, US_EQUITY_MOMENTUM_DAILY,
    US_EQUITY_RETURNS_DAILY, US_EQUITY_UNIVERSE_DAILY,
)


class DataProductContractTests(unittest.TestCase):
    def test_builtin_specs_are_the_catalog_and_provider_registry_contract(self) -> None:
        self.assertFalse((Path("kairos") / "data" / "models.py").exists())
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
                self.assertIn("equity", restored.capabilities["supported_products"])
                self.assertTrue(restored.relative_path)
            self.assertEqual(US_EQUITY_MASSIVE_RAW_DAILY.minimum_publication_level, QualityLevel.RESEARCH)
            self.assertEqual(US_EQUITY_MASSIVE_CORPORATE_ACTIONS.quality_profile, "corporate_action")
            self.assertEqual(US_EQUITY_MASSIVE_CORPORATE_ACTIONS.storage_kind, DatasetStorageKind.REFERENCE)
            self.assertEqual(US_EQUITY_MASSIVE_IDENTITY.quality_profile, "equity_identity")
            self.assertEqual(US_EQUITY_MASSIVE_IDENTITY.storage_kind, DatasetStorageKind.REFERENCE)
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
            providers = default_provider_registry(directory, connector_config=config)

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
            providers = default_provider_registry(directory, connector_config=config)

            self.assertEqual(catalog.product_spec(str(compiled.key)), compiled)
            self.assertEqual(providers.product_spec(str(compiled.key)), compiled)
            self.assertEqual(compiled.storage_kind, DatasetStorageKind.TABULAR)
            self.assertEqual(compiled.quality_profile, "equity_ohlcv")
            self.assertEqual(compiled.minimum_publication_level, QualityLevel.RESEARCH)
            self.assertEqual(compiled.product.dimensions["view"], "raw")

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


if __name__ == "__main__":
    unittest.main()
