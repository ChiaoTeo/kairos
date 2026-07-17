from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from tempfile import TemporaryDirectory
import unittest

from trading.adapters.massive.decoder import decode_option_snapshots
from trading.domain.identity import InstrumentId
from trading.reference import MappingTargetType, ProviderId, ProviderSymbolMapping, ReferenceCatalog


NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


class MassiveDecoderTests(unittest.TestCase):
    def test_snapshot_keeps_vendor_analytics_separate(self):
        mappings = ReferenceCatalog()
        ticker = "O:SPXW260717P06000000"
        mappings.add_mapping(ProviderSymbolMapping(
            ProviderId("massive"), "options", ticker, MappingTargetType.INSTRUMENT,
            InstrumentId("option:us:SPXW").value, NOW,
        ))
        timestamp = int(NOW.timestamp() * 1_000_000_000)
        event = decode_option_snapshots(({
            "details": {"ticker": ticker, "strike_price": 6000},
            "last_quote": {"bid": 9, "ask": 10, "bid_size": 2, "ask_size": 3, "last_updated": timestamp},
            "greeks": {"delta": -0.25, "gamma": 0.01}, "implied_volatility": 0.2, "open_interest": 100,
        },), mappings, ingested_at=NOW)[0]
        self.assertEqual(event.payload["vendor_implied_volatility"], Decimal("0.2"))
        self.assertEqual(event.payload["vendor_greeks"]["delta"], -0.25)
        self.assertNotIn("internal_implied_volatility", event.payload)


if __name__ == "__main__":
    unittest.main()
