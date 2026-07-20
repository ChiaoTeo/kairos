from __future__ import annotations

import asyncio
from decimal import Decimal
import os
from pathlib import Path
import tempfile
import unittest
from uuid import NAMESPACE_URL, uuid5

from kairospy.ports import Environment
from kairospy.connectors.binance.market_stream import BinanceStreamSession, WebSocketClientConnector, websocket_url
from kairospy.connectors.binance.order_book import (
    BinanceOrderBookSnapshotProvider, BinanceOrderBookSyncService,
)
from kairospy.connectors.binance.rest_transport import UrllibBinanceTransport
from kairospy.connectors.binance.stream import BinanceCanonicalStreamService
from kairospy.contracts import MarketEventKind, QuotePayload
from kairospy.domain.identity import InstrumentId
from kairospy.domain.intent import TargetPositionIntent
from kairospy.market_data import (
    BoundedEventChannel, CanonicalCaptureWriter, CanonicalQuoteProjection, CapturedCanonicalEventSource,
    CanonicalOrderBookProjection,
    IterableEventSource,
)
from kairospy.strategies import CanonicalStrategyEventSession, StrategyContext, StrategyDecision


class PublicQuoteAuditStrategy:
    strategy_id = "public-quote-audit-v1"

    def __init__(self) -> None:
        self._decisions = []

    @property
    def decisions(self):
        return tuple(self._decisions)

    def on_start(self, context):
        return ()

    def on_market(self, context):
        quote = context.market.instruments[0].quote
        midpoint = (quote.bid + quote.ask) / Decimal("2")
        self._decisions.append(StrategyDecision(
            context.now.isoformat(), "observe", f"midpoint={midpoint}",
            (quote.instrument_id.value,),
        ))
        return (TargetPositionIntent(
            uuid5(NAMESPACE_URL, f"{self.strategy_id}:{context.now.isoformat()}"),
            self.strategy_id, quote.instrument_id, Decimal("0"),
            "public live/replay audit emits no-position target",
        ),)

    def on_fill(self, fill, context):
        return ()

    def on_end(self, context):
        return ()


def strategy_context(market):
    return StrategyContext(market, object(), (), object(), approved_capital=Decimal("1"))


@unittest.skipUnless(os.getenv("RUN_BINANCE_PUBLIC_LIVE") == "1", "set RUN_BINANCE_PUBLIC_LIVE=1 for public WebSocket smoke")
class BinancePublicLiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_btcusdt_book_ticker_capture_and_replay(self) -> None:
        instrument = InstrumentId("crypto:binance:spot:BTCUSDT")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = root / "canonical.jsonl"
            output = BoundedEventChannel(8)
            service = BinanceCanonicalStreamService(
                BinanceStreamSession(
                    WebSocketClientConnector(timeout=15),
                    websocket_url(Environment.LIVE, "btcusdt@bookTicker", public_only=True),
                    journal=root / "raw.jsonl",
                ),
                {"BTCUSDT": instrument}, output,
                source_instance="integration-test", stream_id="btcusdt@bookTicker",
                canonical_capture=CanonicalCaptureWriter(
                    canonical, session_id="binance-public-live", source="binance",
                ),
            )

            producer = asyncio.create_task(service.run(message_limit=2))
            observed = [event async for event in output.events()]
            await producer
            replayed = [event async for event in CapturedCanonicalEventSource(canonical).events()]
            live_projection, replay_projection = CanonicalQuoteProjection(), CanonicalQuoteProjection()
            for event in observed:
                live_projection.apply(event)
            for event in replayed:
                replay_projection.apply(event)

            self.assertEqual(len(observed), 2)
            self.assertEqual(replayed, observed)
            self.assertTrue(all(event.kind is MarketEventKind.QUOTE for event in observed))
            self.assertTrue(all(isinstance(event.payload, QuotePayload) for event in observed))
            self.assertTrue(all(event.payload.bid is not None and event.payload.ask is not None
                                for event in observed))
            self.assertTrue(all(event.source_sequence is not None for event in observed))
            self.assertEqual(live_projection.get(instrument), replay_projection.get(instrument))
            self.assertIsNotNone(live_projection.get(instrument).midpoint)
            live_strategy = await CanonicalStrategyEventSession(
                IterableEventSource(observed), PublicQuoteAuditStrategy(), strategy_context,
            ).run()
            replay_strategy = await CanonicalStrategyEventSession(
                IterableEventSource(replayed), PublicQuoteAuditStrategy(), strategy_context,
            ).run()
            self.assertEqual(live_strategy, replay_strategy)
            self.assertEqual(len(live_strategy.decisions), 2)
            self.assertEqual(len(live_strategy.intents), 2)

    async def test_btcusdt_depth_snapshot_delta_alignment_and_replay(self) -> None:
        instrument = InstrumentId("crypto:binance:spot:BTCUSDT")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_channel = BoundedEventChannel(128)
            aligned_channel = BoundedEventChannel(128)
            stream = BinanceCanonicalStreamService(
                BinanceStreamSession(
                    WebSocketClientConnector(timeout=15),
                    websocket_url(Environment.LIVE, "btcusdt@depth@100ms", public_only=True),
                    journal=root / "raw-depth.jsonl",
                ),
                {"BTCUSDT": instrument}, raw_channel,
                source_instance="integration-depth-raw", stream_id="btcusdt@depth@100ms",
            )
            aligned_path = root / "aligned-depth.jsonl"
            sync = BinanceOrderBookSyncService(
                raw_channel,
                BinanceOrderBookSnapshotProvider(
                    UrllibBinanceTransport("https://data-api.binance.vision", timeout=15),
                    "BTCUSDT", instrument, limit=100,
                ),
                aligned_channel,
                source_instance="integration-depth-aligned", stream_id="btcusdt@depth@100ms",
                canonical_capture=CanonicalCaptureWriter(
                    aligned_path, session_id="binance-public-depth-live", source="binance",
                ),
            )

            await asyncio.gather(stream.run(message_limit=25), sync.run())
            observed = [event async for event in aligned_channel.events()]
            replayed = [event async for event in CapturedCanonicalEventSource(aligned_path).events()]
            live_projection = CanonicalOrderBookProjection()
            replay_projection = CanonicalOrderBookProjection()
            for event in observed:
                live_projection.apply(event)
            for event in replayed:
                replay_projection.apply(event)

            self.assertGreaterEqual(len(observed), 2)
            self.assertIs(observed[0].kind, MarketEventKind.ORDER_BOOK_SNAPSHOT)
            self.assertTrue(all(
                event.kind in {MarketEventKind.ORDER_BOOK_SNAPSHOT, MarketEventKind.ORDER_BOOK_DELTA}
                for event in observed
            ))
            self.assertEqual(replayed, observed)
            self.assertEqual(live_projection.get(instrument), replay_projection.get(instrument))
            self.assertTrue(live_projection.get(instrument).valid)
            self.assertLess(live_projection.get(instrument).best_bid, live_projection.get(instrument).best_ask)
            self.assertEqual(sync.metrics.deltas_received,
                             sync.metrics.deltas_published + sync.metrics.stale_deltas)


if __name__ == "__main__":
    unittest.main()
