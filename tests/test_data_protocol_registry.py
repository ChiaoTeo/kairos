from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from kairospy.data import (
    BuiltInDataProductRegistry, DataProtocolRegistry, HistoricalDataRequest, HistoricalDataService,
    LiveDataRequest, LiveDataService,
    default_builtin_protocol_registry,
)
from kairospy.surface.product import _args


class DataProtocolRegistryTests(unittest.TestCase):
    def test_historical_and_live_services_are_product_entrypoints(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "signals.csv"
            source.write_text("date,symbol,value\n2026-01-01,AAPL,1.2\n", encoding="utf-8")

            historical = HistoricalDataService(root)
            added = historical.add(_args(
                root,
                source=source,
                name="research.service_signal",
                time=None,
                protocol=None,
                start=None,
                end=None,
                instrument=[],
            ))
            used = historical.use_builtin(_args(
                root,
                key="market.ohlcv.crypto.binance.btc-usdt.1d",
                as_dataset="research.service_btc_daily",
                start="2026-01-01T00:00:00+00:00",
                end="2026-01-03T00:00:00+00:00",
                dry_run=True,
                list_products=False,
                provider=None,
                venue=None,
                connector_config=None,
                instrument=[],
                refresh=False,
                for_use="workspace",
            ))
            live = LiveDataService(root).connect(_args(
                root,
                source=Path("binance.quote"),
                as_dataset="market.quote.crypto.binance.service-btc-usdt",
                time="timestamp",
                account=None,
                channel="quote",
                instrument=["BTCUSDT"],
                freshness_seconds=5.0,
                for_use="shadow",
                market="spot",
                levels=None,
                interval=None,
            ))

        self.assertEqual(added["dataset"], "research.service_signal")
        self.assertEqual(added["historical"]["status"], "ready_for_workspace")
        self.assertEqual(used["dataset"], "research.service_btc_daily")
        self.assertEqual(used["data_product"], "market.ohlcv.crypto.binance.btc-usdt.1d")
        self.assertEqual(used["default_dataset"], "market.ohlcv.crypto.binance.btc-usdt.1d")
        self.assertEqual(live["source_kind"], "built_in")
        self.assertEqual(live["runtime"]["stream"], "btcusdt@bookTicker")

    def test_historical_service_add_owns_user_file_pipeline(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "signals.csv"
            source.write_text("date,symbol,value\n2026-01-01,AAPL,1.2\n", encoding="utf-8")

            def fail_if_delegated(_args):
                raise AssertionError("HistoricalDataService.add must own the user file pipeline")

            with patch("kairospy.surface.product._data_add_impl", fail_if_delegated):
                payload = HistoricalDataService(root).add(_args(
                    root,
                    source=source,
                    name="research.service_owned_signal",
                    time=None,
                    protocol=None,
                    start=None,
                    end=None,
                    instrument=[],
                ))

        self.assertEqual(payload["dataset"], "research.service_owned_signal")
        self.assertEqual(payload["time"], "date")
        self.assertEqual(payload["historical"]["status"], "ready_for_workspace")

    def test_historical_service_use_builtin_owns_builtin_pipeline(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)

            def fail_if_delegated(_args):
                raise AssertionError("HistoricalDataService.use_builtin must own the built-in pipeline")

            with patch("kairospy.surface.product._data_use_impl", fail_if_delegated):
                payload = HistoricalDataService(root).use_builtin(_args(
                    root,
                    key="market.ohlcv.crypto.binance.btc-usdt.1d",
                    as_dataset="research.service_owned_btc_daily",
                    start="2026-01-01T00:00:00+00:00",
                    end="2026-01-03T00:00:00+00:00",
                    dry_run=True,
                    list_products=False,
                    provider=None,
                    venue=None,
                    connector_config=None,
                    instrument=[],
                    refresh=False,
                    for_use="workspace",
                ))

        self.assertEqual(payload["dataset"], "research.service_owned_btc_daily")
        self.assertEqual(payload["data_product"], "market.ohlcv.crypto.binance.btc-usdt.1d")
        self.assertEqual(payload["default_dataset"], "market.ohlcv.crypto.binance.btc-usdt.1d")
        self.assertEqual(payload["source_kind"], "built_in")

    def test_live_service_connect_owns_live_pipeline(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)

            def fail_if_delegated(_args):
                raise AssertionError("LiveDataService.connect must own the live pipeline")

            with patch("kairospy.surface.product._data_connect_impl", fail_if_delegated):
                payload = LiveDataService(root).connect(_args(
                    root,
                    source=Path("binance.quote"),
                    as_dataset="market.quote.crypto.binance.owned-btc-usdt",
                    time="timestamp",
                    account=None,
                    channel="quote",
                    instrument=["BTCUSDT"],
                    freshness_seconds=5.0,
                    for_use="shadow",
                    market="spot",
                    levels=None,
                    interval=None,
                ))

        self.assertEqual(payload["dataset"], "market.quote.crypto.binance.owned-btc-usdt")
        self.assertEqual(payload["source_kind"], "built_in")
        self.assertEqual(payload["runtime"]["stream"], "btcusdt@bookTicker")

    def test_live_service_reconnect_owns_reconnect_pipeline(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = LiveDataService(root)
            service.connect(_args(
                root,
                source=Path("binance.quote"),
                as_dataset="market.quote.crypto.binance.reconnect-btc-usdt",
                time="timestamp",
                account=None,
                channel="quote",
                instrument=["BTCUSDT"],
                freshness_seconds=5.0,
                for_use="shadow",
                market="spot",
                levels=None,
                interval=None,
            ))

            def fail_if_delegated(_args):
                raise AssertionError("LiveDataService.reconnect must own the reconnect pipeline")

            with patch("kairospy.surface.product._data_reconnect_impl", fail_if_delegated):
                payload = service.reconnect(_args(
                    root,
                    dataset="market.quote.crypto.binance.reconnect-btc-usdt",
                    account=None,
                    channel=None,
                    instrument=[],
                    freshness_seconds=None,
                    market=None,
                    levels=None,
                    interval=None,
                ))

        self.assertEqual(payload["operation"], "reconnect")
        self.assertEqual(payload["dataset"], "market.quote.crypto.binance.reconnect-btc-usdt")
        self.assertEqual(payload["reused_configuration"]["source"], "binance.quote")

    def test_registers_user_named_historical_and_live_protocols(self) -> None:
        class Historical:
            def load(self, request: HistoricalDataRequest):
                return [{"timestamp": "2026-01-01T00:00:00Z", "dataset": request.dataset_id}]

        class Live:
            async def stream(self, request: LiveDataRequest):
                if False:
                    yield {}

        registry = DataProtocolRegistry()
        registry.register_historical("research.history", Historical())
        registry.register_live("research.live", Live())

        self.assertEqual(
            list(registry.historical("research.history").load(HistoricalDataRequest("dataset.one"))),
            [{"timestamp": "2026-01-01T00:00:00Z", "dataset": "dataset.one"}],
        )
        self.assertIsInstance(registry.live("research.live"), Live)

    def test_rejects_empty_protocol_name(self) -> None:
        registry = DataProtocolRegistry()
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            registry.register_historical("", object())

    def test_builtin_registry_resolves_user_facing_alias(self) -> None:
        registry = BuiltInDataProductRegistry.from_default_products()
        product = registry.resolve("massive.equity.ohlcv.1d")

        self.assertEqual(product.key, "market.ohlcv.equity.us.massive.1d.vendor_adjusted")
        self.assertEqual(registry.aliases()["massive.equity.ohlcv.1d"], product.key)

    def test_builtin_live_protocol_exposes_runtime_config(self) -> None:
        products = BuiltInDataProductRegistry.from_default_products().list()
        registry = default_builtin_protocol_registry("/tmp/kairospy-protocol-test", products)
        adapter = registry.live("built_in.live.binance.quote")

        config = adapter.runtime_config(LiveDataRequest(
            "market.quote.crypto.binance.btc-usdt",
            instruments=("BTC-USDT",),
            channel="quote",
        ))

        self.assertEqual(config["provider"], "binance")
        self.assertEqual(config["symbol"], "BTCUSDT")
        self.assertEqual(config["channel"], "bookTicker")
        self.assertEqual(config["stream"], "btcusdt@bookTicker")
        self.assertEqual(config["instrument_id"], "crypto:binance:spot:BTCUSDT")

    def test_builtin_orderbook_protocol_defaults_to_binance_depth_stream(self) -> None:
        products = BuiltInDataProductRegistry.from_default_products().list()
        registry = default_builtin_protocol_registry("/tmp/kairospy-protocol-test", products)
        adapter = registry.live("built_in.live.binance.orderbook")

        config = adapter.runtime_config(LiveDataRequest(
            "market.orderbook.crypto.binance.btc-usdt",
            instruments=("BTCUSDT",),
        ))

        self.assertEqual(config["provider"], "binance")
        self.assertEqual(config["channel"], "depth")
        self.assertEqual(config["stream"], "btcusdt@depth")

    def test_builtin_live_protocol_stream_yields_canonical_rows(self) -> None:
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
                    "e": "bookTicker",
                    "s": "BTCUSDT",
                    "b": "100.0",
                    "a": "101.0",
                    "B": "1.0",
                    "A": "2.0",
                    "E": 1767225600000,
                },))

        async def run():
            products = BuiltInDataProductRegistry.from_default_products().list()
            registry = default_builtin_protocol_registry("/tmp/kairospy-protocol-test", products)
            connector = Connector()
            adapter = registry.live("built_in.live.binance.quote")
            rows = []
            async for row in adapter.stream(LiveDataRequest(
                "market.quote.crypto.binance.btc-usdt",
                instruments=("BTCUSDT",),
                channel="quote",
                params={"connector": connector, "message_limit": 1},
            )):
                rows.append(row)
            return connector, rows

        connector, rows = asyncio.run(run())

        self.assertTrue(connector.urls[0].endswith("/btcusdt@bookTicker"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "quote")
        self.assertEqual(rows[0]["instrument_id"]["value"], "crypto:binance:spot:BTCUSDT")
        self.assertEqual(rows[0]["stream_id"], "btcusdt@bookTicker")


if __name__ == "__main__":
    unittest.main()
