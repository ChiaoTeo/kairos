from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from trading.adapters.binance.datasets import BinanceOptionQuotesDatasetConnector
from trading.adapters.deribit.datasets import (
    DeribitDvolDatasetConnector, DeribitOptionSnapshotDatasetConnector, DeribitOptionTradesDatasetConnector,
)
from trading.data import AcquisitionRequest, SourceBinding, TimeRange
from trading.data.products import (
    BTC_DERIBIT_OPTION_QUOTES, BTC_DERIBIT_OPTION_TRADES, BTC_DVOL_DAILY, BTC_OPTION_QUOTES_HOURLY,
)
from trading.data.bootstrap import default_provider_registry, register_configured_products
from trading.data import ResearchDataClient


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
    def test_massive_configuration_plans_without_credentials_and_acquire_fails_before_network(self):
        with TemporaryDirectory() as temporary, patch.dict("os.environ", {}, clear=True):
            config = Path(temporary) / "connectors.json"
            config.write_text(json.dumps({"massive_option_products": [{
                "logical_key": "market.events.options.us.test", "underlying": "TEST",
                "option_tickers": ["O:TEST260130C00100000"],
                "dimensions": {"venue": "opra", "asset_class": "option"},
            }]}))
            register_configured_products(temporary, config)
            client = ResearchDataClient(temporary, providers=default_provider_registry(temporary, connector_config=config))
            plan = client.plan("market.events.options.us.test", start=START, end=END,
                               provider="massive", venue="opra")
            self.assertTrue(plan.connector_available)
            self.assertEqual(plan.estimate.cost_class, "entitled")
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
