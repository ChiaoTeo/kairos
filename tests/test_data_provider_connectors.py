from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
from threading import Event
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from kairos.connectors.binance.datasets import (
    BinanceOptionQuotesDatasetConnector, BinanceUsdmPerpetualHourlyDatasetConnector,
)
from kairos.connectors.binance.historical_archive import (
    BinanceUsdmPerpetualHourlyArchiveProvider, GracefulShutdown,
)
from kairos.connectors.deribit.datasets import (
    DeribitDvolDatasetConnector, DeribitOptionSnapshotDatasetConnector, DeribitOptionTradesDatasetConnector,
)
from kairos.data import AcquisitionRequest, SourceBinding, TimeRange
from kairos.data.products import (
    BINANCE_USDM_PERPETUAL_HOURLY, BTC_DERIBIT_OPTION_QUOTES, BTC_DERIBIT_OPTION_TRADES,
    BTC_DVOL_DAILY, BTC_OPTION_QUOTES_HOURLY,
)
from kairos.data.bootstrap import default_provider_registry, register_configured_products
from kairos.data import DatasetClient


START = datetime(2026, 1, 2, tzinfo=timezone.utc)
END = START + timedelta(days=1)


class _Archive:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def fetch(self, *args):
        self.calls += 1
        return self.payload

    def fetch_daily(self, *args):
        self.calls += 1
        return self.payload


class _Snapshot:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def snapshot(self, currency):
        self.calls += 1
        return {"result": "fake"}, self.rows


class ProviderConnectorContractTests(unittest.TestCase):
    def test_binance_graceful_shutdown_bounds_archive_index_requests(self):
        with TemporaryDirectory() as temporary:
            source = Path(temporary)
            reference = source / "provider=binance/dataset=usdm_perpetual_reference/symbol_catalog.json"
            reference.parent.mkdir(parents=True)
            symbols = [f"T{index}USDT" for index in range(20)]
            reference.write_text(json.dumps({"symbols": symbols}))
            stopped = Event()

            def progress(event):
                if event.get("stage") == "index" and event.get("event") == "progress":
                    stopped.set()

            archive = BinanceUsdmPerpetualHourlyArchiveProvider(progress=progress, stop_event=stopped)
            with patch.object(archive, "_list_keys", return_value=[]) as listing:
                with self.assertRaises(GracefulShutdown):
                    archive._monthly_archive_records(source)
            self.assertGreater(listing.call_count, 0)
            self.assertLessEqual(listing.call_count, 12)

    def test_binance_plan_uses_only_official_archive_records_not_symbol_month_cartesian_product(self):
        with TemporaryDirectory() as temporary:
            archive = BinanceUsdmPerpetualHourlyArchiveProvider()
            monthly = [
                {"kind": "monthly", "symbol": "BTCUSDT", "year": 2025, "month": 1, "day": None,
                 "period": date(2025, 1, 1), "key": "btc-jan", "url": "https://example/btc-jan.zip"},
                {"kind": "monthly", "symbol": "ETHUSDT", "year": 2025, "month": 3, "day": None,
                 "period": date(2025, 3, 1), "key": "eth-mar", "url": "https://example/eth-mar.zip"},
            ]
            daily = [
                {"kind": "daily", "symbol": "NEWUSDT", "year": 2025, "month": 4, "day": 2,
                 "period": date(2025, 4, 2), "key": "new-apr-02", "url": "https://example/new.zip"},
            ]
            with patch.object(archive, "_monthly_archive_records", return_value=monthly), \
                    patch.object(archive, "_daily_archive_records", return_value=daily):
                plan = archive.acquisition_plan(
                    ("BTCUSDT", "ETHUSDT", "NEWUSDT"),
                    datetime(2025, 1, 1, tzinfo=timezone.utc),
                    datetime(2025, 5, 1, tzinfo=timezone.utc), Path(temporary), actual_archives=True,
                )
            self.assertEqual(plan["total_tasks"], 3)
            self.assertEqual([item["tasks"] for item in plan["matrix"]], [1, 0, 1, 1])
            self.assertEqual(plan["planned_symbols"], 3)

    def test_binance_graceful_shutdown_stops_scheduling_and_preserves_completed_raw_files(self):
        with TemporaryDirectory() as temporary:
            stopped = Event()

            def progress(event):
                if event.get("stage") == "download" and event.get("event") == "progress":
                    stopped.set()

            archive = BinanceUsdmPerpetualHourlyArchiveProvider(progress=progress, stop_event=stopped)
            start = datetime(2020, 1, 1, tzinfo=timezone.utc)
            end = datetime(2021, 9, 1, tzinfo=timezone.utc)
            with patch("kairos.connectors.binance.historical_archive.download", return_value=self._hourly_zip()) as fetch:
                with self.assertRaisesRegex(GracefulShutdown, "Stopped cleanly"):
                    archive.fetch(("BTCUSDT",), start, end, Path(temporary), actual_archives=False)
            self.assertGreater(fetch.call_count, 0)
            self.assertLessEqual(fetch.call_count, 12)
            self.assertEqual(len(list(Path(temporary).glob("**/payload.zip"))), fetch.call_count)

    @staticmethod
    def _hourly_zip() -> bytes:
        buffer = BytesIO()
        timestamp = int(START.timestamp() * 1000)
        close = int((START + timedelta(hours=1)).timestamp() * 1000) - 1
        row = f"{timestamp},100,110,90,105,5,{close},500,10,2,200,0\n"
        with ZipFile(buffer, "w") as zipped:
            zipped.writestr("BTCUSDT-1h-2026-01.csv", row)
        return buffer.getvalue()

    def test_binance_hourly_raw_cache_is_resumable_and_reports_cache_hits(self):
        with TemporaryDirectory() as temporary:
            events = []
            archive = BinanceUsdmPerpetualHourlyArchiveProvider(progress=events.append)
            source = Path(temporary)
            with patch("kairos.connectors.binance.historical_archive.download", return_value=self._hourly_zip()) as fetch:
                rows = archive.fetch(("BTCUSDT",), START, END, source)
            self.assertEqual(len(rows), 1)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(events[-1]["downloaded"], 1)
            payload = next(source.glob("**/payload.zip"))
            self.assertTrue(payload.exists())
            self.assertTrue(payload.with_name("receipt.json").exists())

            events.clear()
            with patch("kairos.connectors.binance.historical_archive.download",
                       side_effect=AssertionError("cached payload must not be downloaded")) as fetch:
                rows = archive.fetch(("BTCUSDT",), START, END, source)
            self.assertEqual(len(rows), 1)
            self.assertEqual(fetch.call_count, 0)
            self.assertEqual(events[-1]["cached"], 1)

    def test_binance_hourly_failed_partition_can_resume_on_same_command(self):
        with TemporaryDirectory() as temporary:
            archive = BinanceUsdmPerpetualHourlyArchiveProvider()
            source = Path(temporary)
            with patch("kairos.connectors.binance.historical_archive.download",
                       side_effect=TimeoutError("temporary network failure")):
                with self.assertRaisesRegex(RuntimeError, "rerun the same command to resume"):
                    archive.fetch(("BTCUSDT",), START, END, source)
            self.assertFalse(any(source.glob("**/payload.zip")))
            with patch("kairos.connectors.binance.historical_archive.download", return_value=self._hourly_zip()):
                rows = archive.fetch(("BTCUSDT",), START, END, source)
            self.assertEqual(len(rows), 1)
            self.assertTrue(any(source.glob("**/payload.zip")))

    def test_binance_current_month_falls_back_to_resumable_daily_archives(self):
        with TemporaryDirectory() as temporary:
            events = []
            archive = BinanceUsdmPerpetualHourlyArchiveProvider(progress=events.append)
            daily = {
                "kind": "daily", "symbol": "BTCUSDT", "year": START.year,
                "month": START.month, "day": START.day, "period": START.date(),
                "key": "data/futures/um/daily/klines/BTCUSDT/1h/BTCUSDT-1h-2026-01-02.zip",
                "url": "https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1h/BTCUSDT-1h-2026-01-02.zip",
            }
            with patch.object(archive, "_monthly_archive_records", return_value=[]), \
                    patch.object(archive, "_daily_archive_records", return_value=[daily]), \
                    patch("kairos.connectors.binance.historical_archive.download", return_value=self._hourly_zip()):
                rows = archive.fetch(("BTCUSDT",), START, END, Path(temporary), actual_archives=True)
            self.assertEqual(len(rows), 1)
            self.assertEqual(events[-1]["downloaded"], 1)
            self.assertTrue(any(Path(temporary).glob("**/event_day=*/payload.zip")))

    def test_binance_resume_repairs_a_partial_cached_zip(self):
        with TemporaryDirectory() as temporary:
            source = Path(temporary)
            payload = (source / "provider=binance/dataset=usdm_klines/symbol=BTCUSDT/interval=1h"
                       / "event_year=2026/event_month=01/payload.zip")
            payload.parent.mkdir(parents=True)
            payload.write_bytes(b"interrupted partial zip")
            archive = BinanceUsdmPerpetualHourlyArchiveProvider()
            with patch("kairos.connectors.binance.historical_archive.download", return_value=self._hourly_zip()) as fetch:
                rows = archive.fetch(("BTCUSDT",), START, END, source)
            self.assertEqual(len(rows), 1)
            self.assertEqual(fetch.call_count, 1)
            with ZipFile(payload) as zipped:
                self.assertTrue(zipped.namelist())

    def test_binance_symbol_discovery_combines_historical_and_current_perpetuals(self):
        with TemporaryDirectory() as temporary:
            archive = BinanceUsdmPerpetualHourlyArchiveProvider()
            current = {"symbols": [
                {"symbol": "BTCUSDT", "contractType": "PERPETUAL", "quoteAsset": "USDT"},
                {"symbol": "BTCUSDT_260925", "contractType": "CURRENT_QUARTER", "quoteAsset": "USDT"},
                {"symbol": "ETHUSDC", "contractType": "PERPETUAL", "quoteAsset": "USDC"},
            ]}
            with patch.object(archive, "_archive_symbols", return_value=("OLDUSDT",)), \
                    patch("kairos.connectors.binance.historical_archive.download_json", return_value=current):
                symbols = archive.discover_symbols(Path(temporary))
            self.assertEqual(symbols, ("BTCUSDT", "OLDUSDT"))
            catalog = Path(temporary) / "provider=binance/dataset=usdm_perpetual_reference/symbol_catalog.json"
            self.assertTrue(catalog.exists())

    def test_binance_full_market_usdm_hourly_connector_contract(self):
        class Archive:
            def __init__(self):
                self.calls = 0

            def discover_symbols(self, source_root):
                return ("BTCUSDT", "ETHUSDT")

            def fetch(self, symbols, start, end, source_root):
                self.calls += 1
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

        self._assert_contract(
            BINANCE_USDM_PERPETUAL_HOURLY,
            lambda root, source: BinanceUsdmPerpetualHourlyDatasetConnector(root, source),
            Archive(), "binance", "binance",
        )

    def test_massive_configuration_plans_without_credentials_and_acquire_fails_before_network(self):
        with TemporaryDirectory() as temporary, patch.dict("os.environ", {}, clear=True):
            config = Path(temporary) / "connectors.json"
            config.write_text(json.dumps({"massive_option_products": [{
                "logical_key": "market.events.options.us.test", "underlying": "TEST",
                "option_tickers": ["O:TEST260130C00100000"],
                "dimensions": {"venue": "opra", "asset_class": "option"},
            }]}))
            register_configured_products(temporary, config)
            client = DatasetClient(temporary, providers=default_provider_registry(temporary, connector_config=config))
            plan = client.plan("market.events.options.us.test", start=START, end=END,
                               provider="massive", venue="opra")
            self.assertTrue(plan.connector_available)
            self.assertEqual(plan.estimate.cost_class, "entitled")
            with self.assertRaisesRegex(RuntimeError, "MASSIVE_API_KEY"):
                client.acquire(plan)

    def test_massive_equity_configuration_plans_without_credentials_and_acquire_fails_before_network(self):
        with TemporaryDirectory() as temporary, patch.dict("os.environ", {}, clear=True):
            config = Path(temporary) / "connectors.json"
            config.write_text(json.dumps({"massive_equity_products": [{
                "logical_key": "market.ohlcv.equity.us.massive.nvda.1d.raw",
                "ticker": "NVDA",
                "view": "raw",
            }]}))
            register_configured_products(temporary, config)
            client = DatasetClient(temporary, providers=default_provider_registry(temporary, connector_config=config))
            plan = client.plan("market.ohlcv.equity.us.massive.nvda.1d.raw", start=START, end=END,
                               provider="massive", venue="us-securities")
            self.assertTrue(plan.connector_available)
            self.assertEqual(plan.estimate.cost_class, "entitled-rest-bounded-ticker")
            with self.assertRaisesRegex(RuntimeError, "MASSIVE_API_KEY"):
                client.acquire(plan)

    def test_binance_option_connector_contract(self):
        raw = [{
            "date": "2026-01-02", "hour": "00", "symbol": "BTC-260130-100000-C",
            "best_bid_price": "1", "best_ask_price": "2", "best_bid_qty": "3", "best_ask_qty": "4",
            "best_buy_iv": "0.5", "best_sell_iv": "0.6", "mark_price": "1.5", "mark_iv": "0.55",
            "delta": "0.5", "gamma": "0.01", "vega": "2", "theta": "-1",
            "volume_contracts": "5", "openinterest_contracts": "6",
        }]
        self._assert_contract(
            BTC_OPTION_QUOTES_HOURLY,
            lambda root, source: BinanceOptionQuotesDatasetConnector(root, source), _Archive(raw), "binance", "binance",
        )

    def test_deribit_dvol_connector_contract(self):
        self._assert_contract(
            BTC_DVOL_DAILY,
            lambda root, source: DeribitDvolDatasetConnector(root, source),
            _Archive({date(2026, 1, 2): {"open": 50, "high": 55, "low": 45, "close": 52}}),
            "deribit", "deribit",
        )

    def test_deribit_option_trade_connector_contract(self):
        raw = [{
            "instrument_name": "BTC-30JAN26-100000-C", "timestamp": int(START.timestamp() * 1000),
            "trade_id": "trade-1", "price": 0.01, "amount": 1, "direction": "buy", "iv": 55,
            "mark_price": 0.011, "index_price": 95000, "tick_direction": 0,
        }]
        self._assert_contract(
            BTC_DERIBIT_OPTION_TRADES,
            lambda root, source: DeribitOptionTradesDatasetConnector(root, source), _Archive(raw),
            "deribit", "deribit",
        )

    def test_deribit_current_snapshot_connector_contract(self):
        timestamp = START.isoformat().replace("+00:00", "Z")
        rows = [{
            "period_start": timestamp, "period_end": timestamp, "event_time": timestamp,
            "available_time": timestamp, "venue": "deribit", "underlying_id": "BTC-USD",
            "instrument_id": "BTC-30JAN26-100000-C", "expiry": "2026-01-30T08:00:00Z",
            "option_right": "call", "strike": 100000, "bid_price_btc": 0.01,
            "ask_price_btc": 0.02, "mark_iv": 0.55, "underlying_price_usd": 95000,
            "open_interest": 10,
        }]
        self._assert_contract(
            BTC_DERIBIT_OPTION_QUOTES,
            lambda root, source: DeribitOptionSnapshotDatasetConnector(root, source), _Snapshot(rows),
            "deribit", "deribit",
        )

    def _assert_contract(self, product, factory, source, provider, venue):
        with TemporaryDirectory() as temporary:
            connector = factory(temporary, source)
            request = AcquisitionRequest(
                str(product.key), (TimeRange(START, END),), SourceBinding(provider, venue, 100),
            )
            self.assertTrue(connector.supports(str(product.key)))
            self.assertGreaterEqual(connector.estimate(request).requests, 1)
            wrong = AcquisitionRequest(str(product.key), request.missing, SourceBinding("wrong", venue))
            with self.assertRaises(ValueError):
                connector.acquire(wrong)
            first = connector.acquire(request)
            second = connector.acquire(request)
            self.assertEqual(first.release_id, second.release_id)
            self.assertEqual(first.content_hash, second.content_hash)
            self.assertEqual((first.provider, first.venue), (provider, venue))
            self.assertTrue(first.release_id.startswith("ds_"))
            directory = Path(temporary) / first.relative_path
            for name in ("schema", "lineage", "coverage", "quality", "manifest", "capabilities", "usage", "release"):
                self.assertTrue((directory / f"{name}.json").exists(), name)
            lineage = json.loads((directory / "lineage.json").read_text())
            self.assertEqual(lineage["source"]["provider"], provider)
            self.assertEqual(lineage["source"]["venue"], venue)
            self.assertEqual(source.calls, 2)


if __name__ == "__main__":
    unittest.main()
