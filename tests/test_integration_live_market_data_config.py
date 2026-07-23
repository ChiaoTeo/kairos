from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kairospy.data.quality.freshness import live_view_manifest_path, load_live_view_manifest
from kairospy.data.storage.store import DatasetStore
from kairospy.infrastructure.configuration import KairosProjectConfig
from kairospy.integrations.data_products.live_service import LiveDataService
from kairospy.integrations.live_market_data import LiveMarketDataRequest, resolve_live_market_data_source_config


class IntegrationLiveMarketDataConfigTests(unittest.TestCase):
    def test_integration_resolves_ccxt_pro_driver_from_project_config(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_project_config(root, provider="okx", exchange_id="okex")
            config = KairosProjectConfig.discover(root / ".kairos" / "data")

            source = resolve_live_market_data_source_config(
                config,
                LiveMarketDataRequest(
                    "market.orderbook.crypto.okx.spot.btc-usdt",
                    "okx.orderbook",
                    "okx",
                    ("BTCUSDT",),
                    "depth",
                    {"levels": 10},
                ),
            )

            self.assertIsNotNone(source)
            assert source is not None
            self.assertEqual(source.driver, "ccxt-pro")
            self.assertEqual(source.runtime_config["exchange_id"], "okex")
            self.assertEqual(source.runtime_config["symbol"], "BTC/USDT")
            self.assertEqual(source.runtime_config["levels"], 10)

    def test_data_live_connect_delegates_configured_driver_to_integrations(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / ".kairos" / "data"
            _write_project_config(root, provider="binance", exchange_id="binance")

            result = LiveDataService(data_root).connect(Namespace(
                source="binance.orderbook",
                as_dataset=None,
                time="timestamp",
                account=None,
                instrument=["BTCUSDT"],
                channel="depth",
                market=None,
                levels=5,
                interval=None,
                for_use="live",
                freshness_seconds=5.0,
            ))

            manifest = load_live_view_manifest(live_view_manifest_path(data_root, str(result["dataset"]), "default"))
            self.assertEqual(manifest.live_data_plane["driver"], "ccxt-pro")
            self.assertEqual(manifest.live_data_plane["exchange_id"], "binance")
            self.assertEqual(manifest.live_data_plane["symbol"], "BTC/USDT")
            self.assertEqual(manifest.source["provider"], "binance")
            state = json.loads((DatasetStore(data_root).live_path(str(result["dataset"])) / "default" / "state.json").read_text())
            self.assertEqual(state["live_data_plane"]["driver"], "ccxt-pro")


def _write_project_config(root: Path, *, provider: str, exchange_id: str) -> None:
    data_root = root / ".kairos" / "data"
    data_root.mkdir(parents=True)
    (root / "kairos.toml").write_text(
        "\n".join((
            "[project]",
            'name = "test"',
            "",
            "[paths]",
            'lake_root = ".kairos/data"',
            "",
            f"[providers.{provider}.services.live_market_data]",
            'driver = "ccxt-pro"',
            f'exchange_id = "{exchange_id}"',
            "timeout_ms = 30000",
            "",
        )),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
