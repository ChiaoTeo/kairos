from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
from pathlib import Path

from trading.__main__ import main
from trading.catalog.repository import CatalogRepository
from trading.catalog.service import InstrumentCatalog
from trading.domain.identity import AssetId, InstrumentId, VenueId
from trading.domain.instrument import InstrumentDefinition, VenueListing
from trading.domain.product import CryptoSpotSpec, ProductType


class MultiAssetCliTests(unittest.TestCase):
    def test_reference_backtests_and_simulated_reconcile_trade_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main(["--backtest-root", str(root / "backtests"), "backtest", "run", "--strategy", "covered-call"]), 0)
                self.assertIn("conservative: cash=", output.getvalue())
                self.assertIn("stress: cash=", output.getvalue())
            catalog = InstrumentCatalog()
            catalog.add(InstrumentDefinition(
                InstrumentId("crypto:sim:spot:BTCUSDT"), ProductType.CRYPTO_SPOT, "BTCUSDT", AssetId("BTC"), AssetId("USDT"),
                CryptoSpotSpec(AssetId("BTC"), AssetId("USDT"), Decimal("10")),
                (VenueListing(VenueId("simvenue"), "BTCUSDT", "BTCUSDT", Decimal("0.1"), Decimal("0.001"), Decimal("0.001"), Decimal("10")),),
                datetime(2020, 1, 1, tzinfo=timezone.utc),
            ))
            catalog_path = root / "catalog.json"
            CatalogRepository(catalog_path).save(catalog)
            common = ["--catalog-path", str(catalog_path), "--ledger-path", str(root / "ledger.json"), "--event-log-path", str(root / "events.jsonl")]
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([*common, "account", "reconcile", "--venue", "simulated", "--environment", "testnet"]), 0)
                self.assertIn("Matched: True", output.getvalue())
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    *common, "trade", "run", "--strategy", "spot-perp-carry", "--venue", "simulated",
                    "--environment", "testnet", "--instrument", "crypto:sim:spot:BTCUSDT", "--side", "buy",
                    "--quantity", "0.01", "--limit-price", "50000", "--kill-switch-drill",
                ]), 0)
                value = output.getvalue()
                self.assertIn("Accepted:", value)
                self.assertIn("Kill switch:", value)

    def test_live_trade_requires_explicit_confirmation_before_credentials_or_network(self) -> None:
        with self.assertRaisesRegex(SystemExit, "confirm-live"):
            main([
                "trade", "run", "--strategy", "covered-call", "--venue", "binance", "--environment", "live",
                "--instrument", "x", "--side", "buy", "--quantity", "1", "--limit-price", "1",
            ])

    def test_binance_options_testnet_is_rejected_before_credentials_or_network(self) -> None:
        with self.assertRaisesRegex(SystemExit, "live-only"):
            main([
                "trade", "run", "--strategy", "covered-call", "--venue", "binance",
                "--environment", "testnet", "--product", "options",
                "--instrument", "crypto:binance:option:BTC-250628-60000-C",
                "--side", "buy", "--quantity", "1", "--limit-price", "100",
            ])
        with self.assertRaisesRegex(SystemExit, "live-only"):
            main([
                "account", "reconcile", "--venue", "binance", "--environment", "testnet",
                "--product", "options",
            ])


if __name__ == "__main__":
    unittest.main()
