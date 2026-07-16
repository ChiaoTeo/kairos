from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from trading.domain.identity import AssetId, InstrumentId, VenueId
from trading.domain.instrument import InstrumentDefinition, OptionChain, VenueListing
from trading.domain.market_state import MarketState, apply_market_event
from trading.domain.product import ExerciseStyle, IndexSpec, ListedOptionSpec, OptionRight, ProductType, SettlementSession, SettlementType
from trading.research.analyzer import analyze
from trading.research.snapshot import build_snapshot
from trading.research.spec import ResearchSpec
from trading.storage.codec import event_from_primitive


class FixtureReplayTests(unittest.TestCase):
    def test_standardized_events_reproduce_metrics(self) -> None:
        path = Path(__file__).parent / "fixtures" / "market_events.jsonl"
        events = [event_from_primitive(json.loads(line)) for line in path.read_text().splitlines()]
        state = MarketState()
        for event in events:
            apply_market_event(state, event)
        underlying = InstrumentDefinition(
            InstrumentId("index:spx"), ProductType.INDEX, "SPX", None, AssetId("USD"), IndexSpec(AssetId("USD")),
            (VenueListing(VenueId("ibkr"), "1", "SPX", Decimal("0.01"), Decimal("1"), Decimal("1")),),
            datetime(1970, 1, 1, tzinfo=timezone.utc),
        )
        expiry = datetime(2099, 1, 2, 16, tzinfo=timezone.utc)
        option = InstrumentDefinition(
            InstrumentId("listed-option:spxw:2099-01-02:6000:call"), ProductType.LISTED_OPTION, "SPXW", None, AssetId("USD"),
            ListedOptionSpec(underlying.instrument_id, expiry, Decimal("6000"), OptionRight.CALL, ExerciseStyle.EUROPEAN, SettlementType.CASH, SettlementSession.PM, Decimal("100"), expiry),
            (VenueListing(VenueId("ibkr"), "2", "SPXW", Decimal("0.05"), Decimal("1"), Decimal("1")),),
            datetime(1970, 1, 1, tzinfo=timezone.utc),
        )
        chain = OptionChain(underlying.instrument_id, VenueId("ibkr"), "SMART", "SPXW", Decimal("100"), (date(2099, 1, 2),), (Decimal("6000"),))
        snapshot = build_snapshot(
            run_id=UUID("00000000-0000-0000-0000-000000000010"),
            spec=ResearchSpec(max_quote_age_seconds=60),
            underlying=underlying,
            chain=chain,
            selected=(option,),
            state=state,
            now=datetime(2099, 1, 1, 12, 0, 1, tzinfo=timezone.utc),
        )
        result = analyze(snapshot)
        self.assertEqual(result.rows[0].mid, Decimal("10"))
        self.assertEqual(result.rows[0].theta_per_premium, Decimal("-0.2"))
        self.assertEqual(result.completeness_rate, Decimal("1"))
        self.assertEqual(snapshot.snapshot_span_seconds, 0.2)
