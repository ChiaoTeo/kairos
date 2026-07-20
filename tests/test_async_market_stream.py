from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import unittest

from kairos.contracts import MarketEventKind, QuotePayload, canonicalize_market_event
from kairos.data.feed import ReplayEventFeed, ReplaySpec
from kairos.data.contracts import DatasetKey, DatasetRelease, DatasetStorageKind
from kairos.domain.identity import InstrumentId
from kairos.market_data import (
    BoundedEventChannel, ConflatedLatestChannel, ConsumerGap, IterableEventSource,
    MarketEventEnvelope, MarketEventType, OverflowPolicy, StreamOverflow,
)
from kairos.strategies.sma_cross_study_backtest import BarSeries, SmaCrossConfig, backtest_sma_cross, backtest_sma_cross_events
from kairos.domain.market_data import Bar


NOW = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)


class CanonicalMarketContractTests(unittest.TestCase):
    def test_domain_quote_converts_to_stable_typed_contract(self) -> None:
        source_event = MarketEventEnvelope(
            InstrumentId("crypto:binance:spot:BTCUSDT"), NOW, NOW, NOW,
            "binance", "spot.bookTicker", "BTCUSDT", MarketEventType.QUOTE, 7,
            {"bid": Decimal("100.1"), "ask": Decimal("100.2"), "sequence_number": 99},
            receive_time=NOW,
        )

        first = canonicalize_market_event(source_event, source_instance="gateway-1")
        second = canonicalize_market_event(source_event, source_instance="gateway-1")

        self.assertEqual(first, second)
        self.assertEqual(first.kind, MarketEventKind.QUOTE)
        self.assertEqual(first.source_sequence, 99)
        self.assertEqual(first.receive_sequence, 7)
        self.assertIsInstance(first.payload, QuotePayload)
        self.assertEqual(first.payload.bid, Decimal("100.1"))
        self.assertEqual(first.partition_key, "crypto:binance:spot:BTCUSDT")

    def test_contract_rejects_naive_timestamps(self) -> None:
        source_event = MarketEventEnvelope(
            InstrumentId("fixture:asset"), NOW, NOW, NOW, "fixture", "quotes", "X",
            MarketEventType.QUOTE, 0, {"bid": "1", "ask": "2"},
        )
        canonical = canonicalize_market_event(source_event)
        with self.assertRaisesRegex(ValueError, "receive_time"):
            canonical.__class__(
                canonical.message_id, canonical.schema_id, canonical.schema_version, canonical.kind,
                canonical.instrument_id, canonical.payload, canonical.source, canonical.source_instance,
                canonical.stream_id, canonical.partition_key, canonical.event_time,
                datetime(2026, 1, 1), canonical.available_time, canonical.published_time,
            )


class AsyncMarketStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_iterable_source_preserves_order(self) -> None:
        observed = [value async for value in IterableEventSource((1, 2, 3)).events()]
        self.assertEqual(observed, [1, 2, 3])

    async def test_drop_oldest_reports_gap(self) -> None:
        channel = BoundedEventChannel[int](2, overflow=OverflowPolicy.DROP_OLDEST_WITH_GAP)
        await channel.publish(1)
        await channel.publish(2)
        await channel.publish(3)
        await channel.close()

        observed = [value async for value in channel.events()]

        self.assertIsInstance(observed[0], ConsumerGap)
        self.assertEqual(observed[0].dropped, 1)
        self.assertEqual(observed[1:], [2, 3])
        self.assertEqual(channel.metrics.dropped, 1)

    async def test_fail_stream_never_silently_drops(self) -> None:
        channel = BoundedEventChannel[int](1, overflow=OverflowPolicy.FAIL_STREAM)
        await channel.publish(1)
        with self.assertRaises(StreamOverflow):
            await channel.publish(2)

    async def test_blocking_channel_applies_backpressure(self) -> None:
        channel = BoundedEventChannel[int](1)
        await channel.publish(1)
        blocked = asyncio.create_task(channel.publish(2))
        await asyncio.sleep(0)
        self.assertFalse(blocked.done())
        stream = channel.events()
        self.assertEqual(await anext(stream), 1)
        await blocked
        close = asyncio.create_task(channel.close())
        self.assertEqual(await anext(stream), 2)
        await close
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)

    async def test_latest_channel_conflates_by_key(self) -> None:
        channel = ConflatedLatestChannel[str, int](2)
        await channel.publish("btc", 1)
        await channel.publish("btc", 2)
        await channel.publish("eth", 3)
        await channel.close()

        observed = [value async for value in channel.events()]

        self.assertEqual(observed, [2, 3])
        self.assertEqual(channel.metrics.conflated, 1)

    async def test_frozen_replay_uses_canonical_async_event_source(self) -> None:
        source_event = MarketEventEnvelope(
            InstrumentId("fixture:asset"), NOW, NOW, NOW, "fixture", "quotes", "X",
            MarketEventType.QUOTE, 0, {"bid": "1", "ask": "2"},
        )

        class Repository:
            def scan(self, *args, **kwargs):
                yield source_event

        release = DatasetRelease(
            "release-1", DatasetKey("market.fixture"), "1", "market.event.v1", "1",
            "fixture", "1", "canonical/market/release-1", "parquet", content_hash="abc",
            storage_kind=DatasetStorageKind.MARKET_EVENTS,
        )
        feed = ReplayEventFeed(Repository(), ReplaySpec(
            release, NOW, NOW.replace(hour=13), event_types=(MarketEventType.QUOTE,),
        ))

        observed = [event async for event in feed.events()]

        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0].kind, MarketEventKind.QUOTE)
        self.assertEqual(observed[0].source_instance, "release:release-1")

    async def test_real_sma_strategy_has_batch_and_async_event_parity(self) -> None:
        instrument = InstrumentId("fixture:asset")
        bars = tuple(Bar(
            instrument,
            NOW.replace(hour=index),
            NOW.replace(hour=index + 1),
            Decimal(value), Decimal(value), Decimal(value), Decimal(value), Decimal("1"),
        ) for index, value in enumerate(("1", "2", "3", "4")))
        source_events = tuple(MarketEventEnvelope(
            instrument, bar.end, bar.end, bar.end, "fixture", "bars", "X", MarketEventType.BAR, index,
            {"period_start": bar.start, "period_end": bar.end, "open": bar.open, "high": bar.high,
             "low": bar.low, "close": bar.close, "volume": bar.volume},
        ) for index, bar in enumerate(bars))
        source = IterableEventSource(tuple(canonicalize_market_event(event) for event in source_events))
        config = SmaCrossConfig(1, 2, Decimal("100"), Decimal("0"))

        expected = backtest_sma_cross(BarSeries("fixture", bars), config)
        actual = await backtest_sma_cross_events(source, "fixture", config)

        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
