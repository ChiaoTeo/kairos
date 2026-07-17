from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from trading.adapters.base import Environment
from trading.adapters.massive.websocket import MassiveCanonicalStreamService, MassiveLiveStream
from trading.application import (
    ApplicationConfig, AsyncTradingRuntime, ManagedTaskSpec, RuntimePaths, RuntimeStatus, TradingApplication,
)
from trading.domain.identity import InstrumentId
from trading.domain.market_data import Bar
from trading.market_data import BoundedEventChannel, MarketEventEnvelope, MarketEventType
from trading.orchestration.runtime_store import SQLiteRuntimeStore
from trading.strategies.sma_cross import (
    BarSeries, SmaCrossConfig, backtest_sma_cross, backtest_sma_cross_events,
)


NOW = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
INSTRUMENT = InstrumentId("equity:us:TEST")


class FiniteBarWebSocketClient:
    async def messages(self, market, subscriptions):
        yield [{"ev": "AM", "sym": "TEST", "sequence_number": index + 1, "close": value}
               for index, value in enumerate(("1", "2", "3", "4"))]


class AsyncLivePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_supervised_live_style_stream_matches_frozen_backtest(self) -> None:
        bars = tuple(Bar(
            INSTRUMENT, NOW + timedelta(minutes=index), NOW + timedelta(minutes=index + 1),
            Decimal(value), Decimal(value), Decimal(value), Decimal(value), Decimal("1"),
        ) for index, value in enumerate(("1", "2", "3", "4")))
        config = SmaCrossConfig(1, 2, Decimal("100"), Decimal("0"))
        expected = backtest_sma_cross(BarSeries("fixture", bars), config)
        output = BoundedEventChannel(8)
        result = None
        completed = asyncio.Event()

        def decode(message, source_order):
            bar = bars[source_order]
            return (MarketEventEnvelope(
                INSTRUMENT, bar.end, bar.end, bar.end, "massive", "stocks.minute_aggregate", "TEST",
                MarketEventType.BAR, source_order,
                {"period_start": bar.start, "period_end": bar.end, "open": bar.open, "high": bar.high,
                 "low": bar.low, "close": bar.close, "volume": bar.volume,
                 "sequence_number": message["sequence_number"]}, receive_time=bar.end,
            ),)

        async def no_wait(_):
            return None

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = MassiveCanonicalStreamService(
                MassiveLiveStream(FiniteBarWebSocketClient(), root / "capture" / "raw.jsonl", wait=no_wait),
                "stocks", ("AM.TEST",), decode, output, source_instance="massive-live-fixture",
            )

            async def strategy() -> None:
                nonlocal result
                result = await backtest_sma_cross_events(output, "fixture", config)
                completed.set()

            paths = RuntimePaths.under(root)
            application = TradingApplication(
                ApplicationConfig(Environment.PAPER, paths), SQLiteRuntimeStore(paths.runtime_database),
                runtime_id="live-pipeline-fixture",
            )
            runtime = AsyncTradingRuntime(application, (
                ManagedTaskSpec("massive-market-stream", lambda: service.run(stop_after_messages=4),
                                allow_completion=True),
                ManagedTaskSpec("sma-strategy", strategy, allow_completion=True),
            ))

            await runtime.start()
            await asyncio.wait_for(completed.wait(), 1)

            self.assertEqual(application.status, RuntimeStatus.RUNNING)
            self.assertEqual(result, expected)
            self.assertEqual(service.raw_messages, 4)
            self.assertEqual(service.canonical_events, 4)
            self.assertEqual(len((root / "capture" / "raw.jsonl").read_text().splitlines()), 4)
            await runtime.stop()


if __name__ == "__main__":
    unittest.main()
