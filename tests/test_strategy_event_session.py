from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from uuid import NAMESPACE_URL, uuid5

from kairos.contracts import canonical_from_domain_market_data
from kairos.domain.identity import InstrumentId
from kairos.domain.intent import TargetPositionIntent
from kairos.domain.market_data import Quote
from kairos.market_data import CanonicalCaptureWriter, CapturedCanonicalEventSource, IterableEventSource
from kairos.strategies import CanonicalStrategyEventSession, StrategyContext, StrategyDecision


INSTRUMENT = InstrumentId("crypto:binance:spot:BTCUSDT")
NOW = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)


class QuoteTargetStrategy:
    strategy_id = "quote-target-v1"

    def __init__(self, threshold: Decimal = Decimal("100")) -> None:
        self.threshold = threshold
        self._decisions = []

    @property
    def decisions(self):
        return tuple(self._decisions)

    def on_start(self, context):
        return ()

    def on_market(self, context):
        quote = context.market.instruments[0].quote
        midpoint = (quote.bid + quote.ask) / Decimal("2")
        target = Decimal("1") if midpoint < self.threshold else Decimal("0")
        action = "long" if target else "flat"
        self._decisions.append(StrategyDecision(
            context.now.isoformat(), action, f"midpoint={midpoint}",
            (quote.instrument_id.value,),
        ))
        return (TargetPositionIntent(
            uuid5(NAMESPACE_URL, f"{self.strategy_id}:{context.now.isoformat()}:{target}"),
            self.strategy_id, quote.instrument_id, target, f"{action} from governed midpoint",
        ),)

    def on_fill(self, fill, context):
        return ()

    def on_end(self, context):
        return ()


def context(market):
    return StrategyContext(market, object(), (), object(), approved_capital=Decimal("10000"))


def quote_event(index: int, bid: str, ask: str):
    at = NOW + timedelta(seconds=index)
    return canonical_from_domain_market_data(
        Quote(INSTRUMENT, Decimal(bid), Decimal(ask), Decimal("1"), Decimal("1"), at),
        source="binance", source_instance="strategy-fixture", stream_id="btcusdt@bookTicker",
        receive_time=at, published_time=at, source_sequence=index, receive_sequence=index,
    )[0]


class StrategyEventSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_capture_and_replay_have_identical_projection_decision_and_intent_hashes(self):
        events = (
            quote_event(1, "98", "99"),
            quote_event(2, "100", "101"),
            quote_event(3, "97", "98"),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "strategy-session.jsonl"
            writer = CanonicalCaptureWriter(path, session_id="strategy-session", source="binance")
            for event in events:
                writer.append(event)
            writer.finalize()

            live = await CanonicalStrategyEventSession(
                IterableEventSource(events), QuoteTargetStrategy(), context,
            ).run()
            replay = await CanonicalStrategyEventSession(
                CapturedCanonicalEventSource(path), QuoteTargetStrategy(), context,
            ).run()

            self.assertEqual(live, replay)
            self.assertEqual([item.action for item in live.decisions], ["long", "flat", "long"])
            self.assertEqual([item.target_quantity for item in live.intents],
                             [Decimal("1"), Decimal("0"), Decimal("1")])
            self.assertTrue(all(len(value) == 64 for value in (
                live.projection_hash, live.decision_hash, live.intent_hash, live.audit_hash,
            )))

    async def test_event_order_or_strategy_output_changes_audit_hash(self):
        first = quote_event(1, "98", "99")
        second = quote_event(2, "100", "101")
        baseline = await CanonicalStrategyEventSession(
            IterableEventSource((first, second)), QuoteTargetStrategy(), context,
        ).run()
        changed = await CanonicalStrategyEventSession(
            IterableEventSource((first, quote_event(2, "90", "91"))),
            QuoteTargetStrategy(), context,
        ).run()
        self.assertNotEqual(baseline.projection_hash, changed.projection_hash)
        self.assertNotEqual(baseline.decision_hash, changed.decision_hash)
        self.assertNotEqual(baseline.intent_hash, changed.intent_hash)
        self.assertNotEqual(baseline.audit_hash, changed.audit_hash)


if __name__ == "__main__":
    unittest.main()
