from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from kairospy.integrations.connectors.binance.market_stream import BinanceStreamSession, websocket_url
from kairospy.integrations.ports import Environment
from kairospy.integrations.connectors.binance.stream import BinanceCanonicalStreamService
from kairospy.market.canonical import MarketEventKind, QuotePayload, TradePayload
from kairospy.identity import InstrumentId
from kairospy.market.capture import CanonicalCaptureWriter, CapturedCanonicalEventSource
from kairospy.market.stream import BoundedEventChannel


INSTRUMENT = InstrumentId("crypto:binance:spot:BTCUSDT")


class FakeSocket:
    def __init__(self, values):
        self.values = iter(values)
        self.closed = False

    def receive(self):
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.closed = True


class FakeConnector:
    def __init__(self, sessions):
        self.sessions = iter(sessions)
        self.calls = 0

    def connect(self, url):
        self.calls += 1
        return FakeSocket(next(self.sessions))


class BinanceAsyncStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_only_endpoint_uses_market_data_host(self) -> None:
        self.assertIn(
            "data-stream.binance.vision",
            websocket_url(Environment.LIVE, "btcusdt@bookTicker", public_only=True),
        )

    async def test_public_stream_is_raw_journaled_and_canonicalized(self) -> None:
        messages = (
            {"u": 97523605179, "s": "BTCUSDT", "b": "50000", "a": "50001",
             "B": "2", "A": "3"},
            {"e": "trade", "s": "BTCUSDT", "p": "50000.5", "q": "0.1",
             "t": 77, "E": 1752753600100},
        )
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "raw" / "binance.jsonl"
            session = BinanceStreamSession(FakeConnector((messages,)), "wss://fixture", journal=journal)
            output = BoundedEventChannel(4)
            canonical_path = Path(directory) / "canonical" / "binance.jsonl"
            service = BinanceCanonicalStreamService(
                session, {"BTCUSDT": INSTRUMENT}, output,
                source_instance="binance-public-fixture", stream_id="btcusdt@bookTicker+trade",
                canonical_capture=CanonicalCaptureWriter(
                    canonical_path, session_id="binance-public-fixture", source="binance",
                ),
            )

            producer = asyncio.create_task(service.run(message_limit=2))
            observed = [event async for event in output.events()]
            await producer

            self.assertEqual([item.kind for item in observed], [MarketEventKind.QUOTE, MarketEventKind.TRADE])
            self.assertIsInstance(observed[0].payload, QuotePayload)
            self.assertIsInstance(observed[1].payload, TradePayload)
            self.assertEqual(observed[0].source_sequence, 97523605179)
            self.assertEqual(observed[1].source_sequence, 77)
            self.assertEqual(service.raw_messages, 2)
            self.assertEqual(service.canonical_events, 2)
            self.assertEqual(service.ignored_messages, 0)
            self.assertEqual(len(journal.read_text().splitlines()), 2)
            self.assertEqual(len(canonical_path.read_text().splitlines()), 2)
            manifest = canonical_path.with_suffix(".jsonl.manifest.json")
            self.assertTrue(manifest.exists())
            self.assertEqual([item.capture_offset for item in observed], ["raw-line:1", "raw-line:2"])
            replayed = [item async for item in CapturedCanonicalEventSource(canonical_path).events()]
            self.assertEqual(replayed, observed)

    async def test_reconnect_is_counted_without_losing_canonical_delivery(self) -> None:
        first = (ConnectionError("lost"),)
        second = ({"e": "bookTicker", "s": "BTCUSDT", "b": "1", "a": "2",
                   "B": "1", "A": "1", "E": 1752753600000},)
        session = BinanceStreamSession(FakeConnector((first, second)), "wss://fixture", maximum_reconnects=1)
        output = BoundedEventChannel(2)
        service = BinanceCanonicalStreamService(
            session, {"BTCUSDT": INSTRUMENT}, output,
            source_instance="binance-reconnect-fixture", stream_id="btcusdt@bookTicker",
        )

        producer = asyncio.create_task(service.run(message_limit=1))
        observed = [event async for event in output.events()]
        await producer

        self.assertEqual(len(observed), 1)
        self.assertEqual(service.reconnects, 1)
        self.assertIn("transport_reconnected", observed[0].flags)


if __name__ == "__main__":
    unittest.main()
