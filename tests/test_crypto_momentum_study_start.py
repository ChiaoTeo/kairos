from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from kairos.connectors.binance.datasets import BinanceUsdmPerpetualHourlyDatasetConnector
from kairos.data.acquisition import ProviderRegistry
from kairos.data.quality import DatasetQualityService
from kairos.product_workflow import start_governed_study
from kairos.research import open_study


class _HourlyArchive:
    def discover_symbols(self, source_root):
        return ("BTCUSDT", "ETHUSDT")

    def fetch(self, symbols, start, end, source_root):
        rows = []
        cursor = start
        while cursor < end:
            for offset, symbol in enumerate(symbols):
                price = 100 + offset
                rows.append({
                    "symbol": symbol, "period_start": cursor,
                    "open": price, "high": price + 2, "low": price - 2, "close": price + 1,
                    "volume": 10, "close_timestamp": int((cursor + timedelta(hours=1)).timestamp() * 1000),
                    "quote_volume": 1000, "trade_count": 20,
                    "taker_buy_base_volume": 4, "taker_buy_quote_volume": 400,
                })
            cursor += timedelta(hours=1)
        return rows


class CryptoMomentumStudyStartTests(unittest.TestCase):
    def test_one_command_workflow_acquires_governs_binds_and_scaffolds(self):
        with TemporaryDirectory() as temporary:
            providers = ProviderRegistry()
            providers.register(BinanceUsdmPerpetualHourlyDatasetConnector(temporary, _HourlyArchive()))
            args = SimpleNamespace(
                lake_root=temporary, study_id="crypto-hourly-momentum", version="1.0.0",
                dataset="market.ohlcv.crypto.binance.usdm-perpetual.1h",
                start="2026-01-01T00:00:00+00:00", end="2026-01-21T00:00:00+00:00",
                symbol=[], hypothesis="Cross-sectional momentum persists after activation",
            )
            with patch("kairos.data.bootstrap.default_provider_registry", return_value=providers):
                result = start_governed_study(args)

            self.assertTrue(result["acquired"])
            self.assertTrue(result["quality_passed"])
            self.assertEqual(result["symbols"], "full-market")
            self.assertTrue(Path(result["workspace"]).exists())
            self.assertTrue(Path(result["script"]).exists())
            session = open_study(args.study_id, root=temporary)
            description = session.describe()
            self.assertEqual(description["rows"], 20 * 24 * 2)
            self.assertEqual(description["input_hash"], result["content_hash"])
            profile = session.profile()
            self.assertTrue(profile.passed, profile.as_dict())

    def test_full_market_start_upgrades_a_bounded_smoke_release(self):
        with TemporaryDirectory() as temporary:
            providers = ProviderRegistry()
            providers.register(BinanceUsdmPerpetualHourlyDatasetConnector(temporary, _HourlyArchive()))
            common = {
                "lake_root": temporary, "version": "1.0.0",
                "dataset": "market.ohlcv.crypto.binance.usdm-perpetual.1h",
                "start": "2026-01-01T00:00:00+00:00", "end": "2026-01-21T00:00:00+00:00",
                "hypothesis": "Cross-sectional momentum persists after activation",
            }
            with patch("kairos.data.bootstrap.default_provider_registry", return_value=providers):
                smoke = start_governed_study(SimpleNamespace(
                    **common, study_id="bounded-smoke", symbol=["BTCUSDT"],
                ))
                full = start_governed_study(SimpleNamespace(
                    **common, study_id="full-market", symbol=[],
                ))

            self.assertTrue(smoke["acquired"])
            self.assertTrue(full["acquired"])
            self.assertNotEqual(smoke["release_id"], full["release_id"])
            self.assertEqual(open_study("bounded-smoke", root=temporary).describe()["rows"], 20 * 24)
            self.assertEqual(open_study("full-market", root=temporary).describe()["rows"], 20 * 24 * 2)

    def test_full_market_cross_month_release_passes_deterministic_order(self):
        with TemporaryDirectory() as temporary:
            providers = ProviderRegistry()
            providers.register(BinanceUsdmPerpetualHourlyDatasetConnector(temporary, _HourlyArchive()))
            args = SimpleNamespace(
                lake_root=temporary, study_id="cross-month", version="1.0.0",
                dataset="market.ohlcv.crypto.binance.usdm-perpetual.1h",
                start="2026-01-25T00:00:00+00:00", end="2026-02-15T00:00:00+00:00",
                symbol=[], hypothesis="Cross-month partitions preserve deterministic research order",
            )

            with patch("kairos.data.bootstrap.default_provider_registry", return_value=providers):
                result = start_governed_study(args)

            self.assertTrue(result["quality_passed"])
            assessment = DatasetQualityService(temporary).assess(result["release_id"])
            checks = {item.name: item for item in assessment.checks}
            self.assertTrue(checks["deterministic_order"].passed, checks["deterministic_order"])


if __name__ == "__main__":
    unittest.main()
