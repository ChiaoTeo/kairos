from __future__ import annotations

from trading.domain.identity import InstitutionId

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from trading.adapters.base import OrderAck, OrderRequest
from trading.adapters.base import Environment
from trading.application import ApplicationConfig, AsyncTradingRuntime, ManagedTaskSpec, RuntimePaths, TradingApplication
from trading.application.clock import FixedClock
from trading.domain.capability import OrderType
from trading.domain.execution import TradeSide
from trading.domain.identity import AccountKey, AccountType, InstrumentId, VenueId
from trading.domain.order import ExecutionInstructions, TimeInForce
from trading.execution.command import OutboxStatus
from trading.execution.order_state import DurableOrderStatus
from trading.execution.outbox import DurableOrderCommandService, DurableOrderDispatcher
from trading.orchestration.kill_switch import KillSwitch
from trading.orchestration.runtime_store import SQLiteRuntimeStore


NOW = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)


def request() -> OrderRequest:
    return OrderRequest(
        "internal-1", "client-1", "strategy-v1", "intent-1", "correlation-1",
        AccountKey(InstitutionId("simulated"), "account-1", AccountType.SECURITIES_MARGIN),
        InstrumentId("instrument-1"), TradeSide.BUY, Decimal("1"),
        ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("10")),
    )


class RecordingRouter:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.submissions = 0

    def submit(self, value, at):
        self.submissions += 1
        if self.failure is not None:
            raise self.failure
        return OrderAck(
            value.internal_order_id, value.client_order_id, value.strategy_id, value.intent_id,
            value.correlation_id, "venue-1", at,
        )


class DurableOrderOutboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_order_and_command_are_created_atomically_and_dispatched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            router = RecordingRouter()
            dispatcher = DurableOrderDispatcher(store, router, clock=FixedClock(NOW))

            pending = dispatcher.enqueue(request())

            self.assertEqual(pending.status, OutboxStatus.PENDING)
            self.assertEqual(store.order("client-1").status, DurableOrderStatus.PLANNED)  # type: ignore[union-attr]
            self.assertTrue(await dispatcher.dispatch_once())
            completed = store.outbox_commands()[0]
            order = store.order("client-1")
            assert order is not None
            self.assertEqual(completed.status, OutboxStatus.COMPLETED)
            self.assertEqual(completed.attempts, 1)
            self.assertEqual(order.status, DurableOrderStatus.ACKNOWLEDGED)
            self.assertEqual(order.ack.venue_order_id, "venue-1")  # type: ignore[union-attr]
            self.assertEqual(router.submissions, 1)

    async def test_restart_before_dispatch_keeps_one_pending_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.sqlite3"
            first = SQLiteRuntimeStore(path)
            first.enqueue_order_command(request(), NOW)

            restarted = SQLiteRuntimeStore(path)
            router = RecordingRouter()
            dispatcher = DurableOrderDispatcher(restarted, router, clock=FixedClock(NOW))

            self.assertEqual(len(restarted.outbox_commands(OutboxStatus.PENDING)), 1)
            self.assertTrue(await dispatcher.dispatch_once())
            self.assertEqual(router.submissions, 1)
            self.assertFalse(await dispatcher.dispatch_once())

    async def test_crash_after_claim_is_fail_closed_and_not_redispatched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.sqlite3"
            store = SQLiteRuntimeStore(path)
            store.enqueue_order_command(request(), NOW)
            claimed = store.claim_next_order_command(NOW)
            assert claimed is not None

            restarted = SQLiteRuntimeStore(path)
            router = RecordingRouter()
            dispatcher = DurableOrderDispatcher(restarted, router, clock=FixedClock(NOW))

            self.assertEqual(restarted.outbox_commands()[0].status, OutboxStatus.DISPATCHING)
            self.assertEqual(restarted.order("client-1").status, DurableOrderStatus.SUBMITTING)  # type: ignore[union-attr]
            self.assertEqual(len(restarted.unresolved_orders()), 1)
            self.assertFalse(await dispatcher.dispatch_once())
            self.assertEqual(router.submissions, 0)

    async def test_ambiguous_transport_failure_becomes_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            dispatcher = DurableOrderDispatcher(
                store, RecordingRouter(ConnectionError("lost after write")), clock=FixedClock(NOW),
            )
            dispatcher.enqueue(request())

            with self.assertRaises(ConnectionError):
                await dispatcher.dispatch_once()

            self.assertEqual(store.outbox_commands()[0].status, OutboxStatus.UNKNOWN)
            self.assertEqual(store.order("client-1").status, DurableOrderStatus.UNKNOWN)  # type: ignore[union-attr]
            self.assertEqual(len(store.unresolved_orders()), 1)

            ack = OrderAck(
                "internal-1", "client-1", "strategy-v1", "intent-1", "correlation-1", "venue-1", NOW,
            )
            store.transition_order("client-1", DurableOrderStatus.ACKNOWLEDGED, NOW, ack=ack,
                                   reason="REST recovery by client order id")
            self.assertEqual(store.outbox_commands()[0].status, OutboxStatus.COMPLETED)

    async def test_validation_failure_is_terminal_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            dispatcher = DurableOrderDispatcher(
                store, RecordingRouter(ValueError("invalid venue lot")), clock=FixedClock(NOW),
            )
            dispatcher.enqueue(request())

            with self.assertRaises(ValueError):
                await dispatcher.dispatch_once()

            self.assertEqual(store.outbox_commands()[0].status, OutboxStatus.FAILED_TERMINAL)
            self.assertEqual(store.order("client-1").status, DurableOrderStatus.REJECTED)  # type: ignore[union-attr]
            self.assertEqual(store.unresolved_orders(), ())

    async def test_dispatcher_runs_as_a_supervised_runtime_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = RuntimePaths.under(root)
            store = SQLiteRuntimeStore(paths.runtime_database)
            dispatcher = DurableOrderDispatcher(store, RecordingRouter(), clock=FixedClock(NOW))
            dispatcher.enqueue(request())
            application = TradingApplication(
                ApplicationConfig(Environment.PAPER, paths), store, runtime_id="outbox-runtime-fixture",
            )
            runtime = AsyncTradingRuntime(application, (
                ManagedTaskSpec("order-outbox-dispatcher", lambda: dispatcher.run(idle_wait_seconds=0.001)),
            ))

            await runtime.start()
            for _ in range(100):
                if store.outbox_commands()[0].status is OutboxStatus.COMPLETED:
                    break
                await asyncio.sleep(0)

            self.assertEqual(store.outbox_commands()[0].status, OutboxStatus.COMPLETED)
            self.assertEqual(store.order("client-1").status, DurableOrderStatus.ACKNOWLEDGED)  # type: ignore[union-attr]
            await runtime.stop()

    async def test_command_service_enforces_application_and_kill_switch_gates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = RuntimePaths.under(root)
            store = SQLiteRuntimeStore(paths.runtime_database)
            application = TradingApplication(
                ApplicationConfig(Environment.PAPER, paths), store, runtime_id="command-service-fixture",
            )
            switch = KillSwitch((), FixedClock(NOW), store)
            validations = []
            service = DurableOrderCommandService(
                store, application, switch, validations.append, clock=FixedClock(NOW),
            )

            with self.assertRaisesRegex(RuntimeError, "not operational"):
                service.submit(request())
            application.start()
            application.run()
            switch.trigger((), "operator drill")
            with self.assertRaisesRegex(RuntimeError, "kill switch"):
                service.submit(request())

            self.assertEqual(validations, [])
            self.assertEqual(store.outbox_commands(), ())
            application.stop()


if __name__ == "__main__":
    unittest.main()
