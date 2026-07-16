from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from tempfile import TemporaryDirectory
import unittest

from trading.adapters.massive.corporate_actions import MassiveCorporateActionDecoder
from trading.adapters.massive.reference_store import MassiveReferenceStore
from trading.adapters.massive.reference_pipeline import MassiveReferencePipeline
from trading.adapters.massive import MassiveClient, MassiveConfig, MassiveResponse
from trading.catalog.external import ExternalInstrumentMapping, ExternalMappingRepository
from trading.domain.identity import InstrumentId


NOW = datetime(2020, 1, 1, tzinfo=timezone.utc)


class MassiveReferenceTests(unittest.TestCase):
    def setUp(self):
        self.mappings = ExternalMappingRepository("/tmp/nonexistent-massive-reference-test.json")
        self.mappings.add(ExternalInstrumentMapping("massive", "stocks", "AAPL", InstrumentId("equity:us:AAPL"), NOW))

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


if __name__ == "__main__":
    unittest.main()
