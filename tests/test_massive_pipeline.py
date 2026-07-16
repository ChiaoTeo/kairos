from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from trading.adapters.massive import MassiveClient, MassiveConfig, MassiveCuratedSliceBuilder, MassiveOptionDataPipeline, MassiveResponse
from trading.backtest.feed import DatasetRepository
from trading.pricing import ValuationService
from trading.market_data import ParquetMarketEventRepository


class StubTransport:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.urls = []

    def request(self, url, headers, timeout):
        self.urls.append(url)
        return MassiveResponse(200, {}, json.dumps(self.payloads.pop(0)).encode())


class StatusStubTransport(StubTransport):
    def request(self, url, headers, timeout):
        self.urls.append(url)
        status, payload = self.payloads.pop(0)
        return MassiveResponse(status, {}, json.dumps(payload).encode())


class MassivePipelineTests(unittest.TestCase):
    def test_option_pipeline_archives_maps_canonicalizes_and_replays(self):
        start = datetime(2026, 7, 15, 13, 30, tzinfo=timezone.utc)
        end = start + timedelta(minutes=2)
        event_ns = int((start + timedelta(seconds=59)).timestamp() * 1_000_000_000)
        sip_ns = event_ns + 1_000
        ticker = "O:SPXW260717P06000000"
        transport = StubTransport([
            {"request_id": "underlying", "results": {"ticker": "I:SPX", "market": "indices", "primary_exchange": "CBOE"}},
            {"request_id": "aggregates", "results": [{"t": int(start.timestamp() * 1000), "o": 6000, "h": 6001, "l": 5999, "c": 6000, "v": 10}]},
            {"request_id": "contracts", "results": [{"ticker": ticker, "underlying_ticker": "SPX", "contract_type": "put", "exercise_style": "european", "expiration_date": "2026-07-17", "shares_per_contract": 100, "strike_price": 6000}]},
            {"request_id": "exchanges", "results": [{"id": 301, "name": "CBOE"}, {"id": 302, "name": "C2"}]},
            {"request_id": "conditions", "results": [{"id": 209, "name": "regular"}]},
            {"request_id": "quotes", "results": [{"sip_timestamp": sip_ns, "bid_price": 9.5, "ask_price": 10, "bid_size": 2, "ask_size": 3, "sequence_number": 1}]},
            {"request_id": "trades", "results": [{"sip_timestamp": sip_ns, "price": 9.75, "size": 1, "id": "trade-1", "exchange": 301, "conditions": [209], "sequence_number": 2}]},
        ])
        with TemporaryDirectory() as temporary:
            pipeline = MassiveOptionDataPipeline(temporary, MassiveClient(MassiveConfig("secret"), transport), now=lambda: end)
            manifest = pipeline.prepare_options(dataset_id="options.us.massive.spxw.test.v1", underlying="SPX", option_tickers=(ticker,), start=start, end=end)
            self.assertEqual(manifest["rows"], 3)
            self.assertTrue((Path(temporary) / "catalog" / "instruments.json").exists())
            self.assertTrue((Path(temporary) / "reference" / "external_mappings.json").exists())
            self.assertEqual(len(list((Path(temporary) / "source").rglob("receipt.json"))), 7)
            receipt_text = "".join(item.read_text() for item in (Path(temporary) / "source").rglob("receipt.json"))
            self.assertNotIn("secret", receipt_text)
            events = list(ParquetMarketEventRepository(Path(temporary) / "canonical" / "market").scan("options.us.massive.spxw.test.v1", start, end))
            self.assertEqual([item.record_type.value for item in events], ["quote", "trade", "bar"])
            self.assertEqual(events[0].available_time, datetime.fromtimestamp(sip_ns / 1_000_000_000, tz=timezone.utc))
            curated = MassiveCuratedSliceBuilder(temporary, catalog_path=Path(temporary) / "catalog" / "instruments.json",
                                                  dataset_root=Path(temporary) / "datasets").build(
                "options.us.massive.spxw.test.v1", "spxw.massive.slices.v1", start, end, sampling_seconds=60)
            self.assertEqual(curated.manifest.slice_count, 2)
            self.assertIsNotNone(curated.slices[1].instruments[0].quote)
            self.assertEqual(dict(curated.slices[1].reference_prices).popitem()[1], 6000)
            _, valuation = ValuationService(MassiveCuratedSliceBuilder(temporary, catalog_path=Path(temporary) / "catalog" / "instruments.json", dataset_root=Path(temporary) / "datasets").catalog).value(curated.slices[1])
            self.assertEqual(len(valuation.instruments), 1)
            offline = MassiveOptionDataPipeline(temporary, MassiveClient(MassiveConfig("secret"), StubTransport([])), now=lambda: end + timedelta(days=1))
            rebuilt = offline.prepare_options(dataset_id="options.us.massive.spxw.test.v1", underlying="SPX", option_tickers=(ticker,), start=start, end=end)
            self.assertEqual(rebuilt["dataset_sha256"], manifest["dataset_sha256"])

    def test_spxw_pipeline_uses_point_in_time_synthetic_forward_when_index_history_is_forbidden(self):
        start = datetime(2025, 11, 3, 14, 30, tzinfo=timezone.utc)
        end = start + timedelta(minutes=3)
        sip_ns = int((start + timedelta(seconds=59)).timestamp() * 1_000_000_000)
        call = "O:SPXW251103C06000000"
        put = "O:SPXW251103P06000000"
        contracts = [
            {"ticker": call, "underlying_ticker": "SPX", "contract_type": "call", "exercise_style": "european", "expiration_date": "2025-11-03", "shares_per_contract": 100, "strike_price": 6000},
            {"ticker": put, "underlying_ticker": "SPX", "contract_type": "put", "exercise_style": "european", "expiration_date": "2025-11-03", "shares_per_contract": 100, "strike_price": 6000},
        ]
        transport = StatusStubTransport([
            (200, {"request_id": "underlying", "results": {"ticker": "I:SPX", "market": "indices", "primary_exchange": "CBOE"}}),
            (403, {"status": "NOT_AUTHORIZED", "error": "not entitled"}),
            (200, {"request_id": "call-contract", "results": [contracts[0]]}),
            (200, {"request_id": "put-contract", "results": [contracts[1]]}),
            (200, {"request_id": "exchanges", "results": []}),
            (200, {"request_id": "conditions", "results": []}),
            (200, {"request_id": "call-quotes", "results": [{"sip_timestamp": sip_ns, "bid_price": 12, "ask_price": 14, "sequence_number": 1}]}),
            (200, {"request_id": "call-trades", "results": []}),
            (200, {"request_id": "put-quotes", "results": [{"sip_timestamp": sip_ns, "bid_price": 9, "ask_price": 11, "sequence_number": 2}]}),
            (200, {"request_id": "put-trades", "results": []}),
        ])
        with TemporaryDirectory() as temporary:
            pipeline = MassiveOptionDataPipeline(temporary, MassiveClient(MassiveConfig("secret"), transport), now=lambda: end)
            manifest = pipeline.prepare_options(
                dataset_id="options.us.massive.spxw.synthetic-forward.v1", underlying="SPX",
                option_tickers=(call, put), start=start, end=end,
            )
            self.assertEqual(manifest["rows"], 2)
            lineage = ParquetMarketEventRepository(Path(temporary) / "canonical" / "market").metadata(
                "options.us.massive.spxw.synthetic-forward.v1"
            )["lineage"]
            self.assertFalse(lineage["underlying_reference"]["official_history_available"])
            self.assertEqual(lineage["underlying_reference"]["fallback"], "put_call_parity_synthetic_forward")
            self.assertFalse(any(path.is_dir() and not any(path.iterdir()) for path in (Path(temporary) / "source").rglob("*")))
            curated = MassiveCuratedSliceBuilder(
                temporary, catalog_path=Path(temporary) / "catalog" / "instruments.json",
                dataset_root=Path(temporary) / "datasets",
            ).build(
                "options.us.massive.spxw.synthetic-forward.v1", "spxw.synthetic-forward.slices.v1",
                start, end, sampling_seconds=60, max_quote_age_seconds=60,
            )
            self.assertFalse(curated.slices[0].reference_prices)
            self.assertEqual(dict(curated.slices[1].reference_prices).popitem()[1], 6003)
            self.assertTrue(any(issue.code == "synthetic_forward" for issue in curated.slices[1].quality_issues))
            self.assertFalse(curated.slices[2].reference_prices)
            self.assertTrue(any(issue.code == "missing_underlying" for issue in curated.slices[2].quality_issues))
            self.assertIn("reference=official_or_put_call_parity", curated.manifest.source)


if __name__ == "__main__":
    unittest.main()
