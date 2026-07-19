from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
import json
from contextlib import redirect_stdout
from tempfile import TemporaryDirectory
import unittest
from pathlib import Path

from trading.__main__ import main
from trading.adapters.massive.corporate_actions import MassiveCorporateActionDecoder
from trading.adapters.massive.equity_day_aggs import MassiveEquityDayAggPipeline
from trading.adapters.massive.equity_identity import MassiveEquityIdentityResolver
from trading.adapters.massive.reference_store import MassiveReferenceStore
from trading.adapters.massive.reference_pipeline import MassiveReferencePipeline
from trading.adapters.massive import MassiveClient, MassiveConfig, MassiveResponse
from trading.domain.identity import InstrumentId
from trading.reference import MappingTargetType, ProviderId, ProviderSymbolMapping, ReferenceCatalog, ReferenceCatalogRepository
from tests.test_massive_day_aggs import _EquitySource


NOW = datetime(2020, 1, 1, tzinfo=timezone.utc)


class MassiveReferenceTests(unittest.TestCase):
    def setUp(self):
        self.mappings = ReferenceCatalog()
        self.mappings.add_mapping(ProviderSymbolMapping(
            ProviderId("massive"), "stocks", "AAPL", MappingTargetType.INSTRUMENT,
            InstrumentId("equity:us:AAPL").value, NOW,
        ))

    def test_corporate_actions_are_normalized(self):
        decoder = MassiveCorporateActionDecoder(self.mappings)
        split = decoder.splits(({"id": "s1", "ticker": "AAPL", "execution_date": "2026-07-15", "split_from": 1, "split_to": 4},))[0]
        dividend = decoder.dividends(({"id": "d1", "ticker": "AAPL", "ex_dividend_date": "2026-07-15", "pay_date": "2026-07-18", "cash_amount": "0.25", "currency": "USD"},))[0]
        self.assertEqual(split.ratio, Decimal("4"))
        self.assertEqual(dividend.amount_per_share, Decimal("0.25"))

    def test_reference_tables_are_content_addressed(self):
        with TemporaryDirectory() as temporary:
            store = MassiveReferenceStore(temporary)
            first = store.save("conditions", ({"id": 1, "name": "regular"},), source_receipt="source/receipt.json")
            second = store.save("conditions", ({"name": "regular", "id": 1},), source_receipt="source/receipt.json")
            self.assertEqual(first["sha256"], second["sha256"])

    def test_reference_pipeline_accepts_object_and_array_endpoints(self):
        class Transport:
            def __init__(self): self.values = [
                {"request_id": "e", "results": [{"id": 1, "name": "CBOE"}]},
                {"request_id": "c", "results": [{"id": 1, "name": "regular"}]},
                [{"date": "2026-12-25", "status": "closed"}],
            ]
            def request(self, url, headers, timeout): return MassiveResponse(200, {}, __import__("json").dumps(self.values.pop(0)).encode())
        with TemporaryDirectory() as temporary:
            manifests = MassiveReferencePipeline(temporary, MassiveClient(MassiveConfig("secret"), Transport())).sync_code_tables()
            self.assertEqual([item["name"] for item in manifests], ["exchanges", "conditions", "market_holidays"])

    def test_equity_identity_resolver_keeps_ticker_change_and_reuse_point_in_time(self):
        resolver = MassiveEquityIdentityResolver()
        result = resolver.resolve(
            (
                {"ticker": "OLD", "composite_figi": "BBG000SAME", "listing_date": "2020-01-01", "effective_to": "2021-01-01"},
                {"ticker": "NEW", "composite_figi": "BBG000SAME", "listing_date": "2021-01-01"},
                {"ticker": "OLD", "composite_figi": "BBG000OTHER", "listing_date": "2022-01-01"},
            ),
            ({"old_ticker": "OLD", "new_ticker": "NEW", "event_date": "2021-01-01"},),
        )
        at_2020 = datetime(2020, 6, 1, tzinfo=timezone.utc)
        at_2021 = datetime(2021, 6, 1, tzinfo=timezone.utc)
        at_2022 = datetime(2022, 6, 1, tzinfo=timezone.utc)
        old_first = [item for item in result.mappings if item.external_id == "OLD" and item.active_at(at_2020)][0]
        new = [item for item in result.mappings if item.external_id == "NEW" and item.active_at(at_2021)][0]
        old_reused = [item for item in result.mappings if item.external_id == "OLD" and item.active_at(at_2022)][0]
        self.assertEqual(old_first.target_id, new.target_id)
        self.assertNotEqual(old_first.target_id, old_reused.target_id)
        self.assertEqual(result.quarantined, ())

    def test_build_massive_equity_identity_cli_writes_mapping_manifest(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference_rows = root / "reference_rows.json"
            ticker_events = root / "ticker_events.json"
            reference_rows.write_text(json.dumps([
                {"ticker": "OLD", "composite_figi": "BBG000SAME", "listing_date": "2020-01-01", "effective_to": "2021-01-01"},
                {"ticker": "NEW", "composite_figi": "BBG000SAME", "listing_date": "2021-01-01"},
            ]), encoding="utf-8")
            ticker_events.write_text(json.dumps([
                {"old_ticker": "OLD", "new_ticker": "NEW", "event_date": "2021-01-01"},
            ]), encoding="utf-8")

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", temporary,
                    "data", "build-massive-equity-identity",
                    "--reference-rows", str(reference_rows),
                    "--ticker-events", str(ticker_events),
                ]), 0)
                manifest = json.loads(output.getvalue())

            self.assertEqual(manifest["mapping_count"], 2)
            self.assertEqual(manifest["quarantine_count"], 0)
            self.assertTrue((
                root / "reference" / "provider=massive" / "equity_identity"
                / f"version={manifest['sha256']}" / "mappings.json"
            ).exists())

    def test_equity_day_aggs_resolve_instrument_id_from_massive_mapping(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            resolver = MassiveEquityIdentityResolver()
            result = resolver.resolve(({"ticker": "NVDA", "composite_figi": "BBG000BBJQV0", "listing_date": "1999-01-22"},))
            catalog = ReferenceCatalog()
            for mapping in result.mappings:
                catalog.add_mapping(mapping)
            mapping_path = root / "reference" / "catalog.json"
            ReferenceCatalogRepository(mapping_path).save(catalog)
            pipeline = MassiveEquityDayAggPipeline(root, client=object(), mapping_path=mapping_path)
            pipeline.source = _EquitySource(root)
            manifest = pipeline.prepare("equity.raw.test.v1", "NVDA", datetime(2026, 1, 2).date(), datetime(2026, 1, 3).date(), view="raw")

            import pyarrow.parquet as pq
            target = root / "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=raw/dataset=equity.raw.test.v1"
            row = pq.read_table(target / manifest["file"]).to_pylist()[0]
            self.assertEqual(row["instrument_id"], result.mappings[0].target_id)


if __name__ == "__main__":
    unittest.main()
