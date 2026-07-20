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
from kairos.data.freshness import PAPER_LIVE_FRESHNESS_POLICY, evaluate_live_view_freshness
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

        healthy = LiveViewManifest(
            str(BTC_SPOT_DAILY.key),
            "live:healthy",
            contract_hash,
            "connector-hash",
            "available_time",
            ("available_time", "close"),
            {"channel_contract": "BoundedEventChannel", "freshness": {"max_age_seconds": 60}},
            {"kind": "live_connector"},
            "healthy",
            "2026-07-20T00:00:00+00:00",
        )

        self.assertTrue(evaluate_live_view_freshness(healthy, policy=PAPER_LIVE_FRESHNESS_POLICY).passed)


if __name__ == "__main__":
    unittest.main()
