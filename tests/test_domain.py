from __future__ import annotations

import math
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from trading.adapters.ibkr.research import IbkrSpxwResearchAdapter, decimal_or_none
from trading.domain.event import GreeksUpdated, QuoteUpdated, UnderlyingPriceUpdated, envelope
from trading.domain.identity import AssetId, InstrumentId, VenueId
from trading.domain.instrument import InstrumentDefinition, OptionChain, VenueListing
from trading.domain.market_data import Greeks, Quote
from trading.domain.market_state import MarketState, apply_market_event
from trading.domain.product import ExerciseStyle, IndexSpec, ListedOptionSpec, OptionRight, ProductType, SettlementSession, SettlementType
from trading.research.analyzer import analyze
from trading.research.selector import select_expirations, select_instruments, select_strikes
from trading.research.snapshot import build_snapshot
from trading.research.spec import ResearchSpec
from trading.storage.codec import event_from_primitive, event_to_primitive, snapshot_from_primitive, to_primitive


class DomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2099, 1, 1, 12, tzinfo=timezone.utc)
        self.underlying = InstrumentDefinition(
            InstrumentId("index:spx"), ProductType.INDEX, "SPX", None, AssetId("USD"), IndexSpec(AssetId("USD")),
            (VenueListing(VenueId("ibkr"), "1", "SPX", Decimal("0.01"), Decimal("1"), Decimal("1")),),
            datetime(1970, 1, 1, tzinfo=timezone.utc),
        )
        expiry = datetime(2099, 1, 2, 16, tzinfo=timezone.utc)
        self.option = InstrumentDefinition(
            InstrumentId("listed-option:spxw:2099-01-02:6000:call"), ProductType.LISTED_OPTION, "SPXW", None, AssetId("USD"),
            ListedOptionSpec(self.underlying.instrument_id, expiry, Decimal("6000"), OptionRight.CALL, ExerciseStyle.EUROPEAN, SettlementType.CASH, SettlementSession.PM, Decimal("100"), expiry),
            (VenueListing(VenueId("ibkr"), "2", "SPXW", Decimal("0.05"), Decimal("1"), Decimal("1")),),
            datetime(1970, 1, 1, tzinfo=timezone.utc),
        )

    def test_decimal_normalization(self) -> None:
        self.assertIsNone(decimal_or_none(None))
        self.assertIsNone(decimal_or_none(math.nan))
        self.assertIsNone(decimal_or_none(math.inf))
        self.assertEqual(decimal_or_none(1.25), Decimal("1.25"))
        self.assertEqual(decimal_or_none(0), Decimal("0"))

    def test_ibkr_contract_conversion_round_trip(self) -> None:
        converted = IbkrSpxwResearchAdapter._to_contract(self.option)
        self.assertEqual(converted.right, "C")
        self.assertEqual(converted.lastTradeDateOrContractMonth, "20990102")
        self.assertEqual(converted.strike, 6000.0)

    def test_selector_uses_nearest_strike_and_future_expirations(self) -> None:
        strikes = tuple(Decimal(value) for value in (5900, 5950, 6000, 6050, 6100))
        self.assertEqual(select_strikes(strikes, Decimal("6010"), 1), (Decimal("5950"), Decimal("6000"), Decimal("6050")))
        chain = OptionChain(self.underlying.instrument_id, VenueId("ibkr"), "SMART", "SPXW", Decimal("100"), (date(2098, 1, 1), date(2099, 1, 2)), strikes)
        self.assertEqual(select_expirations(chain, 1, today=date(2099, 1, 1)), (date(2099, 1, 2),))
        selected = select_instruments(chain, Decimal("6010"), ResearchSpec(strikes_each_side=1))
        self.assertEqual(len(selected), 6)

    def test_selector_targets_dte_and_samples_moneyness_range(self) -> None:
        today = date(2099, 1, 1)
        chain = OptionChain(
            self.underlying.instrument_id, VenueId("ibkr"), "SMART", "SPXW", Decimal("100"),
            (date(2099, 1, 2), date(2099, 1, 15), date(2099, 1, 22), date(2099, 2, 15)),
            tuple(Decimal(value) for value in range(4800, 6301, 25)),
        )
        expiries = select_expirations(
            chain, 1, today=today, minimum_dte_days=7,
            maximum_dte_days=45, target_dte_days=21,
        )
        self.assertEqual(expiries, (date(2099, 1, 22),))
        strikes = select_strikes(
            chain.strikes, Decimal("6000"), 10,
            minimum_moneyness=0.85, maximum_moneyness=1.05, maximum_strikes=9,
        )
        self.assertEqual(len(strikes), 9)
        self.assertGreaterEqual(strikes[0] / Decimal("6000"), Decimal("0.85"))
        self.assertLessEqual(strikes[-1] / Decimal("6000"), Decimal("1.05"))

    def test_reducer_codec_snapshot_and_metrics(self) -> None:
        state = MarketState()
        events = [
            envelope(UnderlyingPriceUpdated(self.underlying.instrument_id, Decimal("6000")), source="fixture", event_time=self.now),
            envelope(QuoteUpdated(Quote(self.option.instrument_id, Decimal("9"), Decimal("11"), Decimal("4"), Decimal("5"), self.now)), source="fixture", event_time=self.now),
            envelope(GreeksUpdated(Greeks(self.option.instrument_id, Decimal("0.2"), Decimal("0.5"), Decimal("0.01"), Decimal("-2"), Decimal("1"), self.now)), source="fixture", event_time=self.now),
        ]
        for event in events:
            decoded = event_from_primitive(event_to_primitive(event))
            self.assertEqual(decoded, event)
            apply_market_event(state, decoded)
        chain = OptionChain(self.underlying.instrument_id, VenueId("ibkr"), "SMART", "SPXW", Decimal("100"), (date(2099, 1, 2),), (Decimal("6000"),))
        snapshot = build_snapshot(
            run_id=uuid4(), spec=ResearchSpec(max_quote_age_seconds=60), underlying=self.underlying,
            chain=chain, selected=(self.option,), state=state, now=self.now,
        )
        decoded_snapshot = snapshot_from_primitive(to_primitive(snapshot))
        self.assertEqual(decoded_snapshot, snapshot)
        result = analyze(snapshot)
        self.assertEqual(result.rows[0].mid, Decimal("10"))
        self.assertEqual(result.rows[0].spread, Decimal("2"))
        self.assertEqual(result.completeness_rate, Decimal("1"))

    def test_snapshot_reports_missing_and_stale_data(self) -> None:
        state = MarketState()
        apply_market_event(state, envelope(UnderlyingPriceUpdated(self.underlying.instrument_id, Decimal("6000")), source="fixture", event_time=self.now))
        snapshot = build_snapshot(
            run_id=uuid4(), spec=ResearchSpec(max_quote_age_seconds=1), underlying=self.underlying,
            chain=OptionChain(self.underlying.instrument_id, VenueId("ibkr"), "SMART", "SPXW", Decimal("100"), (date(2099, 1, 2),), (Decimal("6000"),)),
            selected=(self.option,), state=state, now=self.now,
        )
        self.assertIn("missing_market_data", {issue.code for issue in snapshot.quality_issues})


if __name__ == "__main__":
    unittest.main()
