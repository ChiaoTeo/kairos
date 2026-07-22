from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from kairospy.identity import AssetId, InstrumentId, VenueId
from kairospy.market.types import OptionChain
from kairospy.market.state import MarketState, apply_market_event
from kairospy.reference.contracts import ExerciseStyle, IndexSpec, ListedOptionSpec, OptionRight, ProductType, SettlementSession, SettlementType
from kairospy.research.capture.option_snapshot_analysis import analyze_option_snapshot
from kairospy.research.capture.snapshot import build_snapshot
from kairospy.research.capture.spec import OptionChainCaptureSpec
from kairospy.infrastructure.storage.codec import event_from_primitive
from kairospy.reference import ReferenceCatalog
from tests.reference_support import publish_test_instrument


class FixtureReplayTests(unittest.TestCase):
    def test_standardized_events_reproduce_metrics(self) -> None:
        path = Path(__file__).parent / "fixtures" / "market_events.jsonl"
        events = [event_from_primitive(json.loads(line)) for line in path.read_text().splitlines()]
        state = MarketState()
        for event in events:
            apply_market_event(state, event)
        catalog = ReferenceCatalog()
        underlying = publish_test_instrument(
            catalog, InstrumentId("index:spx"), ProductType.INDEX, "SPX", IndexSpec(AssetId("USD")),
            AssetId("USD"), VenueId("ibkr"), "SPX", datetime(1970, 1, 1, tzinfo=timezone.utc),
        )
        expiry = datetime(2099, 1, 2, 16, tzinfo=timezone.utc)
        option = publish_test_instrument(
            catalog, InstrumentId("listed-option:spxw:2099-01-02:6000:call"), ProductType.LISTED_OPTION, "SPXW",
            ListedOptionSpec(underlying.instrument_id, expiry, Decimal("6000"), OptionRight.CALL, ExerciseStyle.EUROPEAN, SettlementType.CASH, SettlementSession.PM, Decimal("100"), expiry),
            AssetId("USD"), VenueId("ibkr"), "SPXW", datetime(1970, 1, 1, tzinfo=timezone.utc),
            price_increment=Decimal("0.05"),
        )
        chain = OptionChain(underlying.instrument_id, VenueId("ibkr"), "SMART", "SPXW", Decimal("100"), (date(2099, 1, 2),), (Decimal("6000"),))
        snapshot = build_snapshot(
            run_id=UUID("00000000-0000-0000-0000-000000000010"),
            spec=OptionChainCaptureSpec(max_quote_age_seconds=60),
            underlying=underlying,
            chain=chain,
            selected=(option,),
            state=state,
            now=datetime(2099, 1, 1, 12, 0, 1, tzinfo=timezone.utc),
            catalog=catalog,
        )
        result = analyze_option_snapshot(snapshot)
        self.assertEqual(result.rows[0].mid, Decimal("10"))
        self.assertEqual(result.rows[0].theta_per_premium, Decimal("-0.2"))
        self.assertEqual(result.completeness_rate, Decimal("1"))
        self.assertEqual(snapshot.snapshot_span_seconds, 0.2)
