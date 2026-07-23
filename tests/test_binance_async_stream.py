from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from kairospy.integrations.connectors.binance.market_stream import BinanceStreamSession, websocket_url
from kairospy.integrations.connectors.binance.user_data_stream import (
    BinanceUserDataStreamService,
    BinanceUserFillEventSource,
    BinanceUserStreamProcessor,
    UserFillUpdate,
)
from kairospy.integrations.ports import Environment
from kairospy.integrations.connectors.binance.stream import BinanceCanonicalStreamService
from kairospy.market.canonical import MarketEventKind, QuotePayload, TradePayload
from kairospy.identity import InstrumentId
from kairospy.market.capture import CanonicalCaptureWriter, CapturedCanonicalEventSource
from kairospy.market.stream import BoundedEventChannel
from kairospy.identity import AccountRef, AccountType, AssetId, InstitutionId


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
        self.urls = []

    def connect(self, url):
        self.calls += 1
        self.urls.append(url)
        return FakeSocket(next(self.sessions))


class FakeUserStreamTransport:
    def __init__(self) -> None:
        self.calls = []

    def request(self, method, path, params=None, headers=None):
        self.calls.append((method, path, params, headers))
        if method == "POST":
            return {"listenKey": "listen-key-fixture"}
        return {}


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

    async def test_user_fill_event_source_yields_fills_from_listen_key_stream(self) -> None:
        account = AccountRef(InstitutionId("binance"), "main", AccountType.CRYPTO_SPOT)
        transport = FakeUserStreamTransport()
        connector = FakeConnector(((
            {
                "e": "executionReport",
                "x": "TRADE",
                "X": "FILLED",
                "s": "BTCUSDT",
                "t": 501,
                "i": 701,
                "c": "client-1",
                "S": "BUY",
                "l": "0.5",
                "L": "50000",
                "n": "1.25",
                "N": "USDT",
                "E": 1752753600100,
            },
            EOFError("closed"),
        ),))
        source = BinanceUserFillEventSource(
            BinanceUserDataStreamService(transport, "api-key"),
            BinanceUserStreamProcessor(account, {"BTCUSDT": INSTRUMENT}),
            environment=Environment.LIVE,
            connector=connector,
            message_limit=1,
        )

        observed = [event async for event in source.events()]

        self.assertEqual(len(observed), 1)
        self.assertIsInstance(observed[0], UserFillUpdate)
        self.assertEqual(observed[0].execution_id, "501")
        self.assertEqual(observed[0].client_order_id, "client-1")
        self.assertEqual(observed[0].commission_asset, AssetId("USDT"))
        self.assertTrue(observed[0].fully_filled)
        self.assertEqual(source.listen_key, "listen-key-fixture")
        self.assertEqual(connector.urls, ["wss://stream.binance.com/ws/listen-key-fixture"])
        self.assertEqual(transport.calls[0][0:2], ("POST", "/api/v3/userDataStream"))
        self.assertEqual(transport.calls[-1][0:2], ("DELETE", "/api/v3/userDataStream"))

    async def test_options_user_fill_event_source_yields_fills_from_options_stream(self) -> None:
        account = AccountRef(InstitutionId("binance"), "main", AccountType.DERIVATIVES)
        instrument = InstrumentId("crypto:binance:option:BTC-200730-9000-C")
        transport = FakeUserStreamTransport()
        connector = FakeConnector(((
            {
                "e": "ORDER_TRADE_UPDATE",
                "E": 1752753600100,
                "o": [{
                    "s": "BTC-200730-9000-C",
                    "c": "client-option-1",
                    "oid": "4611875134427365377",
                    "S": "BUY",
                    "X": "FILLED",
                    "fi": [{
                        "t": "20",
                        "p": "1000",
                        "q": "1",
                        "T": 1752753600101,
                        "m": "TAKER",
                        "f": "0.001",
                        "a": "USDT",
                    }],
                }],
            },
            EOFError("closed"),
        ),))
        source = BinanceUserFillEventSource(
            BinanceUserDataStreamService(transport, "api-key", options=True),
            BinanceUserStreamProcessor(account, {"BTC-200730-9000-C": instrument}),
            environment=Environment.LIVE,
            connector=connector,
            options=True,
            message_limit=1,
        )

        observed = [event async for event in source.events()]

        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0].execution_id, "20")
        self.assertEqual(observed[0].order_id, "4611875134427365377")
        self.assertEqual(observed[0].client_order_id, "client-option-1")
        self.assertEqual(observed[0].instrument_id, instrument)
        self.assertEqual(observed[0].quantity, Decimal("1"))
        self.assertEqual(observed[0].price, Decimal("1000"))
        self.assertEqual(observed[0].commission, Decimal("0.001"))
        self.assertEqual(observed[0].commission_asset, AssetId("USDT"))
        self.assertTrue(observed[0].fully_filled)
        self.assertEqual(connector.urls, ["wss://nbstream.binance.com/eoptions/ws/listen-key-fixture"])
        self.assertEqual(transport.calls[0][0:2], ("POST", "/eapi/v1/listenKey"))
        self.assertEqual(transport.calls[-1][0:2], ("DELETE", "/eapi/v1/listenKey"))


if __name__ == "__main__":
    unittest.main()
