from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import json
import tempfile
import unittest

from kairospy.contracts import canonical_from_domain_market_data
from kairospy.domain.identity import InstrumentId
from kairospy.domain.market_data import Quote
from kairospy.market_data import (
    CaptureResourceExceeded, RotatingCanonicalCaptureWriter,
    RotatingCapturedCanonicalEventSource,
)


INSTRUMENT = InstrumentId("crypto:binance:spot:BTCUSDT")
NOW = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)


def event(index: int):
    at = NOW + timedelta(milliseconds=index)
    return canonical_from_domain_market_data(
        Quote(INSTRUMENT, Decimal("100"), Decimal("101"), Decimal("1"), Decimal("1"), at),
        source="binance", source_instance="rotation-fixture", stream_id="btcusdt@bookTicker",
        receive_time=at, published_time=at, source_sequence=index, receive_sequence=index,
    )[0]


class RotatingCanonicalCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_rotates_verifies_and_replays_all_segments_in_global_order(self):
        events = tuple(event(index) for index in range(7))
        with tempfile.TemporaryDirectory() as directory:
            writer = RotatingCanonicalCaptureWriter(
                Path(directory) / "session.canonical.jsonl",
                session_id="rotation-fixture", source="binance",
                maximum_segment_events=3, maximum_segment_bytes=1024 * 1024,
            )
            for item in events:
                writer.append(item)
            manifest = writer.finalize()

            self.assertEqual(manifest.segment_count, 3)
            self.assertEqual([item.event_count for item in manifest.segments], [3, 3, 1])
            self.assertEqual(manifest.event_count, 7)
            self.assertEqual(manifest.total_bytes, sum(Path(item.event_path).stat().st_size
                                                       for item in manifest.segments))
            replayed = [item async for item in RotatingCapturedCanonicalEventSource(
                writer.manifest_path,
            ).events()]
            self.assertEqual(replayed, list(events))

    async def test_manifest_tampering_and_total_disk_budget_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            writer = RotatingCanonicalCaptureWriter(
                path, session_id="budget-fixture", source="binance",
                maximum_segment_events=100, maximum_segment_bytes=1000,
                maximum_total_bytes=1000,
            )
            with self.assertRaises(CaptureResourceExceeded):
                for index in range(100):
                    writer.append(event(index))
            writer.finalize()

            raw = json.loads(writer.manifest_path.read_text())
            raw["segments"][0]["event_count"] += 1
            writer.manifest_path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError, "manifest hash"):
                RotatingCapturedCanonicalEventSource(writer.manifest_path)


if __name__ == "__main__":
    unittest.main()
