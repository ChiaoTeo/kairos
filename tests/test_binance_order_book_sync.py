from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from kairospy.connectors.binance.order_book import (
    BinanceOrderBookSnapshotProvider, BinanceOrderBookSyncFault, BinanceOrderBookSyncService,
)
from kairospy.contracts import MarketEventKind, canonical_from_domain_market_data
from kairospy.domain.identity import InstrumentId
from kairospy.domain.market_data import OrderBookDelta, OrderBookLevel, OrderBookSnapshot
from kairospy.market_data import (
    BoundedEventChannel, CanonicalCaptureWriter, CanonicalOrderBookProjection,
    CapturedCanonicalEventSource, IterableEventSource,
)


INSTRUMENT = InstrumentId("crypto:binance:spot:BTCUSDT")
NOW = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)


def delta(first: int, last: int, offset: int = 0):
    value = OrderBookDelta(
        INSTRUMENT, (OrderBookLevel(Decimal("100"), Decimal(str(1 + offset))),), (),
        first, last, NOW + timedelta(milliseconds=offset),
    )
    return canonical_from_domain_market_data(
        value, source="binance", source_instance="raw-depth", stream_id="btcusdt@depth",
        receive_time=value.event_time, published_time=value.event_time,
        receive_sequence=offset,
    )[0]


def snapshot(sequence: int, offset: int = 0):
    return OrderBookSnapshot(
        INSTRUMENT, (OrderBookLevel(Decimal("100"), Decimal("1")),),
        (OrderBookLevel(Decimal("101"), Decimal("1")),), sequence,
        NOW + timedelta(milliseconds=offset),
    )


class FakeSnapshotProvider:
    def __init__(self, snapshots):
        self.snapshots = iter(snapshots)
        self.calls = 0

    async def fetch(self):
        self.calls += 1
        return next(self.snapshots)


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, path, params=None, headers=None):
        self.calls.append((method, path, params, headers))
        return self.response


class BinanceOrderBookSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_provider_uses_public_depth_endpoint(self) -> None:
        transport = FakeTransport({
            "lastUpdateId": 42,
            "bids": [["100", "2"]],
            "asks": [["101", "3"]],
        })
        provider = BinanceOrderBookSnapshotProvider(transport, "btcusdt", INSTRUMENT, limit=100)
        observed = await provider.fetch()
        self.assertEqual(observed.sequence, 42)
        self.assertEqual(observed.bids[0].quantity, Decimal("2"))
        self.assertEqual(transport.calls, [
            ("GET", "/api/v3/depth", {"symbol": "BTCUSDT", "limit": 100}, None),
        ])

    async def test_initial_buffer_alignment_drops_stale_and_publishes_bridge(self) -> None:
        source = IterableEventSource((delta(8, 9), delta(10, 11, 1), delta(12, 12, 2)))
        provider = FakeSnapshotProvider((snapshot(10),))
        output = BoundedEventChannel(8)
        service = BinanceOrderBookSyncService(
            source, provider, output, source_instance="aligned-depth", stream_id="btcusdt@depth",
        )
        await service.run()
        observed = [event async for event in output.events()]

        self.assertEqual(
            [event.kind for event in observed],
            [MarketEventKind.ORDER_BOOK_SNAPSHOT, MarketEventKind.ORDER_BOOK_DELTA,
             MarketEventKind.ORDER_BOOK_DELTA],
        )
        self.assertEqual([event.source_sequence for event in observed], [10, 11, 12])
        self.assertEqual(service.metrics.stale_deltas, 1)
        self.assertEqual(service.metrics.deltas_published, 2)
        self.assertTrue(service.projection.get(INSTRUMENT).valid)

    async def test_gap_triggers_snapshot_and_current_delta_is_retried(self) -> None:
        source = IterableEventSource((delta(11, 11), delta(14, 14, 1), delta(15, 15, 2)))
        provider = FakeSnapshotProvider((snapshot(10), snapshot(13, 1)))
        output = BoundedEventChannel(8)
        service = BinanceOrderBookSyncService(
            source, provider, output, source_instance="aligned-depth", stream_id="btcusdt@depth",
        )
        await service.run()
        observed = [event async for event in output.events()]

        self.assertEqual([event.source_sequence for event in observed], [10, 11, 13, 14, 15])
        self.assertEqual(provider.calls, 2)
        self.assertEqual(service.metrics.gaps, 1)
        self.assertEqual(service.metrics.resyncs, 1)
        self.assertEqual(service.projection.gaps[0].expected_sequence, 12)
        self.assertEqual(service.projection.get(INSTRUMENT).sequence, 15)

    async def test_transport_reconnect_forces_snapshot_even_if_sequence_is_contiguous(self) -> None:
        reconnected = replace(delta(12, 12, 1), flags=("transport_reconnected",))
        source = IterableEventSource((delta(11, 11), reconnected))
        provider = FakeSnapshotProvider((snapshot(10), snapshot(11, 1)))
        output = BoundedEventChannel(8)
        service = BinanceOrderBookSyncService(
            source, provider, output, source_instance="aligned-depth", stream_id="btcusdt@depth",
        )
        await service.run()
        observed = [event async for event in output.events()]

        self.assertEqual([event.source_sequence for event in observed], [10, 11, 11, 12])
        self.assertEqual(service.metrics.resyncs, 1)
        self.assertEqual(provider.calls, 2)

    async def test_unbridgeable_gap_fails_after_bounded_resync(self) -> None:
        service = BinanceOrderBookSyncService(
            IterableEventSource((delta(20, 20),)),
            FakeSnapshotProvider((snapshot(10), snapshot(10, 1))),
            BoundedEventChannel(4),
            source_instance="aligned-depth", stream_id="btcusdt@depth",
            maximum_resync_attempts=1,
        )
        with self.assertRaisesRegex(BinanceOrderBookSyncFault, "unable to bridge") as raised:
            await service.run()
        self.assertEqual(raised.exception.code, "resync_exhausted")
        self.assertEqual(service.metrics.resyncs, 1)

    async def test_aligned_capture_replay_rebuilds_same_book(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aligned.jsonl"
            output = BoundedEventChannel(8)
            service = BinanceOrderBookSyncService(
                IterableEventSource((delta(11, 11), delta(12, 12, 1))),
                FakeSnapshotProvider((snapshot(10),)), output,
                source_instance="aligned-depth", stream_id="btcusdt@depth",
                canonical_capture=CanonicalCaptureWriter(
                    path, session_id="aligned-depth", source="binance",
                ),
            )
            await service.run()
            live_events = [event async for event in output.events()]
            replay_events = [event async for event in CapturedCanonicalEventSource(path).events()]
            live = CanonicalOrderBookProjection()
            replay = CanonicalOrderBookProjection()
            for event in live_events:
                live.apply(event)
            for event in replay_events:
                replay.apply(event)
            self.assertEqual(replay_events, live_events)
            self.assertEqual(replay.get(INSTRUMENT), live.get(INSTRUMENT))

    async def test_buffered_delta_keeps_event_time_but_advances_strategy_availability(self) -> None:
        late_snapshot = snapshot(10, 10_000)
        output = BoundedEventChannel(8)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aligned.jsonl"
            service = BinanceOrderBookSyncService(
                IterableEventSource((delta(11, 11),)), FakeSnapshotProvider((late_snapshot,)), output,
                source_instance="aligned-depth", stream_id="btcusdt@depth",
                canonical_capture=CanonicalCaptureWriter(
                    path, session_id="availability-fixture", source="binance",
                ),
            )
            await service.run()
            observed = [event async for event in output.events()]
            self.assertEqual(len(observed), 2)
            self.assertLess(observed[1].event_time, observed[0].event_time)
            self.assertGreaterEqual(observed[1].available_time, observed[0].available_time)
            self.assertIn("snapshot_aligned", observed[1].flags)
            self.assertEqual([event async for event in CapturedCanonicalEventSource(path).events()], observed)


if __name__ == "__main__":
    unittest.main()
