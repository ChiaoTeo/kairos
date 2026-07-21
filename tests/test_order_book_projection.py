from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from kairospy.contracts import canonical_from_trading_market_data
from kairospy.trading.identity import InstrumentId
from kairospy.trading.market_data import OrderBookDelta, OrderBookLevel, OrderBookSnapshot
from kairospy.market_data import (
    CanonicalCaptureWriter, CanonicalOrderBookProjection, CapturedCanonicalEventSource,
)


INSTRUMENT = InstrumentId("crypto:binance:spot:BTCUSDT")
NOW = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)


def canonical(value, receive_sequence: int):
    return canonical_from_trading_market_data(
        value, source="binance", source_instance="book-fixture", stream_id="btcusdt@depth",
        receive_time=value.event_time, published_time=value.event_time,
        receive_sequence=receive_sequence,
    )[0]


class CanonicalOrderBookProjectionTests(unittest.IsolatedAsyncioTestCase):
    def test_snapshot_delta_delete_and_overlap_are_deterministic(self) -> None:
        projection = CanonicalOrderBookProjection()
        snapshot = canonical(OrderBookSnapshot(
            INSTRUMENT,
            (OrderBookLevel(Decimal("99"), Decimal("2")), OrderBookLevel(Decimal("100"), Decimal("1"))),
            (OrderBookLevel(Decimal("102"), Decimal("2")), OrderBookLevel(Decimal("101"), Decimal("1"))),
            10, NOW,
        ), 0)
        state = projection.apply(snapshot)
        self.assertTrue(state.valid)
        self.assertEqual((state.best_bid, state.best_ask), (Decimal("100"), Decimal("101")))

        delta = canonical(OrderBookDelta(
            INSTRUMENT,
            (OrderBookLevel(Decimal("100"), Decimal("0")), OrderBookLevel(Decimal("100.5"), Decimal("3"))),
            (OrderBookLevel(Decimal("101"), Decimal("4")),),
            9, 11, NOW + timedelta(milliseconds=1),
        ), 1)
        state = projection.apply(delta)
        self.assertEqual(state.sequence, 11)
        self.assertEqual((state.best_bid, state.best_ask), (Decimal("100.5"), Decimal("101")))
        self.assertEqual(state.asks[0].quantity, Decimal("4"))
        self.assertEqual(projection.gaps, ())

        self.assertIsNone(projection.apply(delta))
        self.assertEqual(projection.version, 2)

    def test_gap_fails_closed_until_a_new_snapshot(self) -> None:
        projection = CanonicalOrderBookProjection()
        projection.apply(canonical(OrderBookSnapshot(
            INSTRUMENT, (OrderBookLevel(Decimal("100"), Decimal("1")),),
            (OrderBookLevel(Decimal("101"), Decimal("1")),), 20, NOW,
        ), 0))
        gap_event = canonical(OrderBookDelta(
            INSTRUMENT, (OrderBookLevel(Decimal("100"), Decimal("2")),), (),
            23, 24, NOW + timedelta(seconds=1),
        ), 1)
        invalid = projection.apply(gap_event)
        self.assertFalse(invalid.valid)
        self.assertEqual((invalid.bids, invalid.asks), ((), ()))
        self.assertIn("expected=21", invalid.invalid_reason)
        self.assertEqual(projection.gaps[0].expected_sequence, 21)

        ignored = projection.apply(canonical(OrderBookDelta(
            INSTRUMENT, (), (OrderBookLevel(Decimal("101"), Decimal("2")),),
            25, 25, NOW + timedelta(seconds=2),
        ), 2))
        self.assertEqual(ignored, invalid)

        recovered = projection.apply(canonical(OrderBookSnapshot(
            INSTRUMENT, (OrderBookLevel(Decimal("99"), Decimal("5")),),
            (OrderBookLevel(Decimal("102"), Decimal("6")),), 30,
            NOW + timedelta(seconds=3),
        ), 3))
        self.assertTrue(recovered.valid)
        self.assertEqual(recovered.sequence, 30)
        self.assertIsNone(recovered.invalid_reason)

    def test_delta_before_snapshot_and_crossed_book_are_never_strategy_visible(self) -> None:
        projection = CanonicalOrderBookProjection()
        missing = projection.apply(canonical(OrderBookDelta(
            INSTRUMENT, (OrderBookLevel(Decimal("100"), Decimal("1")),), (),
            1, 1, NOW,
        ), 0))
        self.assertFalse(missing.valid)
        self.assertEqual(missing.invalid_reason, "snapshot_required")

        crossed = projection.apply(canonical(OrderBookSnapshot(
            INSTRUMENT, (OrderBookLevel(Decimal("102"), Decimal("1")),),
            (OrderBookLevel(Decimal("101"), Decimal("1")),), 2, NOW + timedelta(seconds=1),
        ), 1))
        self.assertFalse(crossed.valid)
        self.assertEqual(crossed.invalid_reason, "crossed_snapshot")
        self.assertEqual((crossed.bids, crossed.asks), ((), ()))

    async def test_capture_replay_rebuilds_identical_book_and_gap_evidence(self) -> None:
        events = (
            canonical(OrderBookSnapshot(
                INSTRUMENT, (OrderBookLevel(Decimal("100"), Decimal("1")),),
                (OrderBookLevel(Decimal("101"), Decimal("1")),), 50, NOW,
            ), 0),
            canonical(OrderBookDelta(
                INSTRUMENT, (OrderBookLevel(Decimal("100"), Decimal("2")),), (),
                51, 51, NOW + timedelta(seconds=1),
            ), 1),
            canonical(OrderBookDelta(
                INSTRUMENT, (), (OrderBookLevel(Decimal("101"), Decimal("2")),),
                53, 53, NOW + timedelta(seconds=2),
            ), 2),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.canonical.jsonl"
            writer = CanonicalCaptureWriter(path, session_id="book-fixture", source="binance")
            live = CanonicalOrderBookProjection()
            for event in events:
                writer.append(event)
                live.apply(event)
            writer.finalize()

            replay = CanonicalOrderBookProjection()
            replayed = [event async for event in CapturedCanonicalEventSource(path).events()]
            for event in replayed:
                replay.apply(event)

            self.assertEqual(replayed, list(events))
            self.assertEqual(replay.get(INSTRUMENT), live.get(INSTRUMENT))
            self.assertEqual(replay.gaps, live.gaps)


if __name__ == "__main__":
    unittest.main()
