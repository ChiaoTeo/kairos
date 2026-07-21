from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from kairospy.contracts import canonicalize_market_event
from kairospy.trading.identity import InstrumentId
from kairospy.market_data import (
    BoundedEventChannel, MarketEventEnvelope, MarketEventType, run_binance_market_soak,
    run_binance_market_restart_campaign,
)


class StopSession:
    def __init__(self) -> None:
        self.stopped = asyncio.Event()

    def stop(self) -> None:
        self.stopped.set()


class ContinuousFixtureService:
    def __init__(self, output, counter=None) -> None:
        self.output = output
        self.session = StopSession()
        self.stream_id = "fixture@bookTicker"
        self.reconnects = 0
        self.raw_messages = 0
        self.canonical_events = 0
        self.ignored_messages = 0
        self.counter = counter

    async def run(self) -> None:
        sequence = self.counter[0] if self.counter is not None else 0
        try:
            while not self.session.stopped.is_set():
                sequence += 1
                if self.counter is not None:
                    self.counter[0] = sequence
                self.raw_messages += 1
                now = datetime.now(timezone.utc)
                event = MarketEventEnvelope(
                    InstrumentId("fixture:asset"), now, now, now, "binance", self.stream_id, "FIXTURE",
                    MarketEventType.QUOTE, sequence,
                    {"bid": Decimal("1"), "ask": Decimal("2"), "sequence_number": sequence},
                    receive_time=now,
                )
                await self.output.publish(canonicalize_market_event(event))
                self.canonical_events += 1
                await asyncio.sleep(0.001)
        finally:
            await self.output.close()


class MarketDataSoakTests(unittest.IsolatedAsyncioTestCase):
    async def test_soak_writes_audited_acceptance_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = BoundedEventChannel(64)
            service = ContinuousFixtureService(output)
            result = await run_binance_market_soak(
                service, output, duration_seconds=0.03, minimum_events=5,
                maximum_silence_seconds=0.1, artifact_path=Path(directory) / "soak.json",
            )

            self.assertTrue(result.passed)
            self.assertGreaterEqual(result.event_count, 5)
            self.assertEqual(result.sequence_regressions, 0)
            self.assertEqual(result.channel_dropped, 0)
            self.assertLessEqual(result.peak_channel_utilization, result.maximum_channel_utilization)
            self.assertTrue(Path(result.artifact).exists())
            self.assertEqual(len(result.audit_hash), 64)

    async def test_soak_fails_when_acceptance_threshold_is_not_met(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = BoundedEventChannel(64)
            result = await run_binance_market_soak(
                ContinuousFixtureService(output), output, duration_seconds=0.01, minimum_events=1000,
                maximum_silence_seconds=0.1, artifact_path=Path(directory) / "soak.json",
            )

            self.assertFalse(result.passed)
            self.assertIn("below minimum", result.failures[0])

    async def test_restart_campaign_proves_continuity_across_fresh_stream_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            counter = [0]

            def factory(index):
                output = BoundedEventChannel(64)
                return ContinuousFixtureService(output, counter), output

            result = await run_binance_market_restart_campaign(
                factory, stream_id="fixture@bookTicker", duration_seconds=0.05,
                restart_interval_seconds=0.02, minimum_events=10,
                maximum_silence_seconds=0.1,
                artifact_path=Path(directory) / "campaign.json",
            )
            self.assertTrue(result.passed)
            self.assertEqual(result.leg_count, 3)
            self.assertEqual(result.restart_count, 2)
            self.assertEqual(result.boundary_sequence_regressions, 0)
            self.assertTrue(result.restart_drill_passed)
            self.assertTrue(all(Path(item).exists() for item in result.leg_artifacts))

    async def test_restart_campaign_rejects_sequence_reset_between_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def factory(index):
                output = BoundedEventChannel(64)
                return ContinuousFixtureService(output), output

            result = await run_binance_market_restart_campaign(
                factory, stream_id="fixture@bookTicker", duration_seconds=0.04,
                restart_interval_seconds=0.02, minimum_events=5,
                maximum_silence_seconds=0.1,
                artifact_path=Path(directory) / "campaign.json",
            )
            self.assertFalse(result.passed)
            self.assertEqual(result.boundary_sequence_regressions, 1)


if __name__ == "__main__":
    unittest.main()
