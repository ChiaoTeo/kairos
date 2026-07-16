from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from trading.backtest.feed import MarketSlice
from trading.domain.identity import AssetId, InstrumentId, VenueId
from trading.domain.instrument import InstrumentDefinition, VenueListing
from trading.domain.market_data import Greeks, Quote
from trading.domain.product import ExerciseStyle, IndexSpec, ListedOptionSpec, OptionRight, ProductType, SettlementSession, SettlementType
from trading.research.retention import DeltaLegWatchlist
from trading.research.snapshot import InstrumentSnapshot


def option(strike: str, expiry: datetime) -> InstrumentDefinition:
    instrument_id = InstrumentId(f"option:spxw:{strike}")
    return InstrumentDefinition(
        instrument_id, ProductType.LISTED_OPTION, "SPXW", None, AssetId("USD"),
        ListedOptionSpec(
            InstrumentId("index:spx"), expiry, Decimal(strike), OptionRight.PUT,
            ExerciseStyle.EUROPEAN, SettlementType.CASH, SettlementSession.PM,
            Decimal("100"), expiry,
        ),
        (VenueListing(VenueId("ibkr"), strike, strike, Decimal("0.05"), Decimal("1"), Decimal("1")),),
        datetime(2020, 1, 1, tzinfo=timezone.utc),
    )


class OptionLegRetentionTests(unittest.TestCase):
    def test_delta_legs_persist_across_processes_until_exit_dte(self) -> None:
        ny = ZoneInfo("America/New_York")
        expiry = datetime(2026, 8, 7, 16, tzinfo=ny)
        definitions = (option("5600", expiry), option("5700", expiry), option("5800", expiry))
        deltas = (Decimal("-0.10"), Decimal("-0.25"), Decimal("-0.40"))
        at = datetime(2026, 7, 17, 15, 30, tzinfo=ny)
        snapshots = tuple(
            InstrumentSnapshot(
                definition.instrument_id,
                Quote(definition.instrument_id, Decimal("4"), Decimal("5"), Decimal("10"), Decimal("10"), at), at,
                None, None,
                Greeks(definition.instrument_id, Decimal("0.2"), delta, Decimal("0.01"), Decimal("-1"), Decimal("2"), at), at,
            )
            for definition, delta in zip(definitions, deltas)
        )
        market = MarketSlice(at, snapshots, ((InstrumentId("index:spx"), Decimal("6000")),), available_instruments=tuple(item.instrument_id for item in definitions))
        with tempfile.TemporaryDirectory() as directory:
            watchlist = DeltaLegWatchlist(directory, "real-study")
            self.assertTrue(watchlist.observe(market, definitions))
            self.assertFalse(watchlist.observe(market, definitions))
            restored = DeltaLegWatchlist(directory, "real-study")
            known = {item.instrument_id: item for item in definitions}
            active = restored.active_definitions(datetime(2026, 8, 4, 12, tzinfo=ny), known)
            expired = restored.active_definitions(datetime(2026, 8, 5, 12, tzinfo=ny), known)
        self.assertEqual({item.product_spec.strike for item in active}, {Decimal("5600"), Decimal("5700")})
        self.assertEqual(expired, ())


if __name__ == "__main__":
    unittest.main()
