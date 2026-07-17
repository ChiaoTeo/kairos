from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from trading.data.bootstrap import (
    configured_product_specs, default_provider_registry, register_configured_products,
    register_default_products,
)
from trading.data.catalog import DataCatalog
from trading.data.models import DatasetStorageKind, QualityLevel
from trading.data.products import BTC_SPOT_DAILY


class DatasetProductSpecTests(unittest.TestCase):
    def test_builtin_specs_are_the_catalog_and_provider_registry_contract(self) -> None:
        from trading.data import models
        from trading.data.products import ManagedDataset

        self.assertFalse(hasattr(models, "Datasets"))
        self.assertIs(ManagedDataset, type(BTC_SPOT_DAILY))
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

    def test_product_spec_rejects_unsafe_physical_layout(self) -> None:
        from dataclasses import replace

        with self.assertRaisesRegex(ValueError, "safe lake-relative"):
            replace(BTC_SPOT_DAILY, relative_path="../outside")


if __name__ == "__main__":
    unittest.main()
