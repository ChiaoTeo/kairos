from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from kairospy.application import (
    ApplicationConfig, AsyncServiceSupervisor, AsyncKairosRuntime, ManagedServiceSpec, ManagedServiceStatus,
    RuntimePaths, RuntimeStatus, ServiceCriticality, KairosApplication,
)
from kairospy.ports import Environment
from kairospy.trading.capability import MarketDataCapabilities, MarketDataKind
from kairospy.trading.identity import AssetId, InstrumentId, VenueId
from kairospy.trading.product import EquitySpec, ProductType
from kairospy.reference import ReferenceCatalog
from tests.reference_support import publish_test_instrument
from kairospy.market_data import (
    CapturePolicy, DeliveryMode, MarketDataRequirement, SubscriptionAction,
    SubscriptionPlanner, SubscriptionReconciler,
)
from kairospy.orchestration.runtime_store import SQLiteRuntimeStore


NOW = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
VENUE = VenueId("fixture")
INSTRUMENT = InstrumentId("equity:us:TEST")


class AsyncServiceSupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_supervisor_owns_and_stops_long_running_tasks(self) -> None:
        stopped = asyncio.Event()

        async def service() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        supervisor = AsyncServiceSupervisor()
        await supervisor.start((ManagedServiceSpec("market-stream", service),))

        self.assertTrue(supervisor.healthy)
        self.assertEqual(supervisor.snapshots()[0].status, ManagedServiceStatus.RUNNING)
        await supervisor.stop()
        self.assertTrue(stopped.is_set())
        self.assertEqual(supervisor.snapshots()[0].status, ManagedServiceStatus.STOPPED)

    async def test_async_runtime_binds_task_health_to_durable_application_state(self) -> None:
        fail = asyncio.Event()

        async def critical_stream() -> None:
            await fail.wait()
            raise ConnectionError("private stream lost")

        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths.under(Path(directory))
            application = KairosApplication(
                ApplicationConfig(Environment.PAPER, paths), SQLiteRuntimeStore(paths.runtime_database),
                runtime_id="async-runtime-fixture",
            )
            runtime = AsyncKairosRuntime(application, (ManagedServiceSpec("private-stream", critical_stream),))

            await runtime.start()
            self.assertEqual(application.status, RuntimeStatus.RUNNING)
            fail.set()
            fault = await runtime.wait_for_critical_fault()

            self.assertEqual(fault.task_name, "private-stream")
            self.assertEqual(application.status, RuntimeStatus.REDUCE_ONLY)
            persisted = application.store.runtime_state(KairosApplication.STATE_KEY)
            self.assertEqual(persisted["status"], RuntimeStatus.REDUCE_ONLY.value)
            await runtime.stop()
            self.assertEqual(application.status, RuntimeStatus.STOPPED)

    async def test_critical_failure_is_observable(self) -> None:
        async def failure() -> None:
            raise ConnectionError("stream disconnected")

        supervisor = AsyncServiceSupervisor()
        await supervisor.start((ManagedServiceSpec("private-stream", failure),))
        fault = await supervisor.wait_critical_fault()

        self.assertEqual(fault.task_name, "private-stream")
        self.assertEqual(fault.error_type, "ConnectionError")
        self.assertFalse(supervisor.healthy)
        self.assertEqual(supervisor.snapshots()[0].status, ManagedServiceStatus.FAILED)
        await supervisor.stop()

    async def test_restart_policy_is_bounded_and_audited(self) -> None:
        attempts = 0

        async def recover_once() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise EOFError("temporary disconnect")

        supervisor = AsyncServiceSupervisor()
        await supervisor.start((ManagedServiceSpec(
            "recovering-stream", recover_once, ServiceCriticality.IMPORTANT,
            restart_limit=1, allow_completion=True,
        ),))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        snapshot = supervisor.snapshots()[0]
        self.assertEqual(snapshot.status, ManagedServiceStatus.COMPLETED)
        self.assertEqual(snapshot.attempts, 2)
        self.assertEqual(snapshot.restart_count, 1)
        self.assertEqual(snapshot.last_fault.error_type, "EOFError")
        await supervisor.stop()


class SubscriptionPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = ReferenceCatalog()
        publish_test_instrument(self.catalog, INSTRUMENT, ProductType.EQUITY, "TEST", EquitySpec("NASDAQ", "US", AssetId("USD")), AssetId("USD"), VENUE, "TEST", datetime(2020, 1, 1, tzinfo=timezone.utc))
        self.planner = SubscriptionPlanner(self.catalog, {VENUE: MarketDataCapabilities(
            frozenset({MarketDataKind.QUOTE, MarketDataKind.TRADE}), frozenset({ProductType.EQUITY}),
        )})

    def test_multiple_consumers_merge_to_one_subscription(self) -> None:
        requirements = (
            MarketDataRequirement("strategy-a", VENUE, (INSTRUMENT,), (MarketDataKind.QUOTE,),
                                  DeliveryMode.LATEST, 10, capture=CapturePolicy.CANONICAL),
            MarketDataRequirement("strategy-b", VENUE, (INSTRUMENT,), (MarketDataKind.QUOTE,),
                                  DeliveryMode.ORDERED, 5, capture=CapturePolicy.RAW_AND_CANONICAL),
        )

        plan = self.planner.build(requirements, NOW)

        self.assertEqual(len(plan.subscriptions), 1)
        subscription = plan.subscriptions[0]
        self.assertEqual(subscription.consumers, ("strategy-a", "strategy-b"))
        self.assertEqual(subscription.delivery, DeliveryMode.ORDERED)
        self.assertEqual(subscription.maximum_age_seconds, 5)
        self.assertEqual(subscription.capture, CapturePolicy.RAW_AND_CANONICAL)

    def test_reconciliation_is_incremental_and_reconnect_restores_target(self) -> None:
        quote = MarketDataRequirement("strategy", VENUE, (INSTRUMENT,), (MarketDataKind.QUOTE,))
        quote_trade = MarketDataRequirement(
            "strategy", VENUE, (INSTRUMENT,), (MarketDataKind.QUOTE, MarketDataKind.TRADE),
        )
        first, second = self.planner.build((quote,), NOW), self.planner.build((quote_trade,), NOW)
        reconciler = SubscriptionReconciler()

        initial = reconciler.commands(first)
        reconciler.commit(first)
        incremental = reconciler.commands(second)
        reconciler.commit(second)
        reconciler.reset_after_disconnect()
        restored = reconciler.commands(second)

        self.assertEqual([item.action for item in initial], [SubscriptionAction.SUBSCRIBE])
        self.assertEqual(len(incremental), 1)
        self.assertEqual(incremental[0].key.kind, MarketDataKind.TRADE)
        self.assertEqual(len(restored), 2)
        self.assertTrue(all(item.action is SubscriptionAction.SUBSCRIBE for item in restored))

    def test_capability_and_listing_fail_before_network(self) -> None:
        unsupported = MarketDataRequirement(
            "strategy", VENUE, (INSTRUMENT,), (MarketDataKind.ORDER_BOOK,), depth=10,
        )
        with self.assertRaisesRegex(ValueError, "does not support market data"):
            self.planner.build((unsupported,), NOW)

        missing = MarketDataRequirement(
            "strategy", VenueId("missing"), (INSTRUMENT,), (MarketDataKind.QUOTE,),
        )
        with self.assertRaisesRegex(LookupError, "no market data connector"):
            self.planner.build((missing,), NOW)


if __name__ == "__main__":
    unittest.main()
