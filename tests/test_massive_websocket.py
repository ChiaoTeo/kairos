from __future__ import annotations

import asyncio
import json
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest

from datetime import datetime, timezone
from decimal import Decimal

from kairospy.integrations.connectors.massive.websocket import MassiveCanonicalStreamService, MassiveLiveStream
from kairospy.market.canonical import MarketEventKind, QuotePayload
from kairospy.identity import InstrumentId
from kairospy.market.source_events import MarketEventEnvelope, MarketEventType
from kairospy.market.stream import BoundedEventChannel


class FakeWebSocketClient:
    def __init__(self):
        self.calls = 0

    async def messages(self, market, subscriptions):
        self.calls += 1
        if self.calls == 1:
            yield [{"ev": "Q", "sym": "AAPL", "q": 1}]
            raise ConnectionError("disconnect")
        yield [{"ev": "Q", "sym": "AAPL", "q": 3}]


class MassiveWebSocketTests(unittest.TestCase):
    def test_reconnect_journal_gap_and_backfill_hooks(self):
        async def scenario(directory):
            gaps, backfills, consumed = [], [], []
            faults = []
            async def no_wait(_): pass
            async def on_gap(key, expected, actual): gaps.append((key, expected, actual))
            async def backfill(): backfills.append(True)
            async def on_fault(fault): faults.append(fault)
            async def consume(message): consumed.append(message)
            journal = Path(directory) / "raw.jsonl"
            stream = MassiveLiveStream(FakeWebSocketClient(), journal, wait=no_wait, on_gap=on_gap,
                                       on_reconnect_backfill=backfill, on_fault=on_fault)
            await stream.run("stocks", ("Q.AAPL",), consume, stop_after_messages=2)
            self.assertEqual(len(consumed), 2)
            self.assertEqual(gaps, [("Q:AAPL", 2, 3)])
            self.assertEqual(len(backfills), 1)
            self.assertEqual(faults[0].error_type, "ConnectionError")
            self.assertEqual(len(journal.read_text().splitlines()), 2)
        with TemporaryDirectory() as directory:
            asyncio.run(scenario(directory))

    def test_supervised_service_publishes_canonical_events(self):
        async def scenario(directory):
            async def no_wait(_): pass
            now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)

            def decode(message, source_order):
                return (MarketEventEnvelope(
                    InstrumentId("equity:us:AAPL"), now, now, now, "massive", "stocks.quotes", "AAPL",
                    MarketEventType.QUOTE, source_order,
                    {"bid": Decimal(str(message["q"])), "ask": Decimal(str(message["q"] + 1)),
                     "sequence_number": message["q"]}, receive_time=now,
                ),)

            output = BoundedEventChannel(4)
            stream = MassiveLiveStream(FakeWebSocketClient(), Path(directory) / "raw.jsonl", wait=no_wait)
            service = MassiveCanonicalStreamService(
                stream, "stocks", ("Q.AAPL",), decode, output, source_instance="massive-test",
            )
            producer = asyncio.create_task(service.run(stop_after_messages=2))
            observed = [event async for event in output.events()]
            await producer

            self.assertEqual(len(observed), 2)
            self.assertTrue(all(event.kind is MarketEventKind.QUOTE for event in observed))
            self.assertTrue(all(isinstance(event.payload, QuotePayload) for event in observed))
            self.assertEqual([event.source_sequence for event in observed], [1, 3])
            self.assertEqual(service.raw_messages, 2)
            self.assertEqual(service.canonical_events, 2)

        with TemporaryDirectory() as directory:
            asyncio.run(scenario(directory))


if __name__ == "__main__":
    unittest.main()
