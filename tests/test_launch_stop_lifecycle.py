from __future__ import annotations

import asyncio
import unittest

from kairospy.application.support.launch.application.launcher import bind_stop_signal
from kairospy.application.usecases.market.domain.subscriptions import MarketDataSubscriptionSpec
from kairospy.application.usecases.market.application.runtime import build_market_runtime
from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.domain.market import TradePrint
from kairospy.domain.reference import MarketRef


class LaunchStopLifecycleTests(unittest.TestCase):
    def test_launch_binds_the_explicit_stop_signal_capability(self) -> None:
        class MarketData:
            def __init__(self) -> None:
                self.signal = None

            def set_stop_signal(self, signal):
                self.signal = signal

        market_data = MarketData()
        stop_requested = lambda: True

        bind_stop_signal(market_data, stop_requested)

        self.assertIs(market_data.signal, stop_requested)

    def test_launch_rejects_a_market_data_service_without_stop_capability(self) -> None:
        with self.assertRaisesRegex(TypeError, "set_stop_signal"):
            bind_stop_signal(object(), lambda: True)

    def test_streaming_market_data_stops_when_feed_is_silent(self) -> None:
        async def scenario() -> None:
            subscribed = asyncio.Event()
            never = asyncio.Event()
            unsubscribed: list[str] = []

            class Remote:
                subscription_id = "remote-1"

                async def events(self):
                    await never.wait()
                    yield None

            class Feed:
                venue = "binance"

                async def subscribe(self, request):
                    subscribed.set()
                    return Remote()

                async def unsubscribe(self, subscription_id: str) -> None:
                    unsubscribed.append(subscription_id)

            data = build_market_runtime(
                source_name="test",
                stream_connections={"binance": Feed()},
                market_service=MarketApplication(),
            )
            data.subscribe(
                MarketDataSubscriptionSpec(
                    market=MarketRef.ephemeral(
                        venue="binance",
                        market="spot",
                        source_symbol="BTCUSDT",
                    ),
                    selectors=(TradePrint,),
                    identity="test.stop",
                )
            )
            should_stop = False
            data.set_stop_signal(lambda: should_stop)

            events_task = asyncio.create_task(data.events().__anext__())
            await asyncio.wait_for(subscribed.wait(), timeout=1.0)
            should_stop = True

            with self.assertRaises(StopAsyncIteration):
                await asyncio.wait_for(events_task, timeout=1.5)
            self.assertEqual(unsubscribed, ["remote-1"])

        asyncio.run(scenario())

    def test_streaming_market_data_cancels_a_blocked_subscription(self) -> None:
        async def scenario() -> None:
            subscribed = asyncio.Event()
            cancelled = asyncio.Event()

            class Feed:
                venue = "binance"

                async def subscribe(self, request):
                    subscribed.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        cancelled.set()
                        raise

                async def unsubscribe(self, subscription_id: str) -> None:
                    return None

            data = build_market_runtime(
                source_name="test",
                stream_connections={"binance": Feed()},
                market_service=MarketApplication(),
            )
            data.subscribe(
                MarketDataSubscriptionSpec(
                    market=MarketRef.ephemeral(
                        venue="binance",
                        market="spot",
                        source_symbol="BTCUSDT",
                    ),
                    selectors=(TradePrint,),
                    identity="test.blocked-subscribe",
                )
            )
            should_stop = False
            data.set_stop_signal(lambda: should_stop)
            events_task = asyncio.create_task(data.events().__anext__())
            await asyncio.wait_for(subscribed.wait(), timeout=1.0)
            should_stop = True

            with self.assertRaises(StopAsyncIteration):
                await asyncio.wait_for(events_task, timeout=1.5)
            self.assertTrue(cancelled.is_set())

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
