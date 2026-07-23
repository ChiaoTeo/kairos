from __future__ import annotations

from kairospy.identity import InstitutionId

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from kairospy.integrations.ports import OrderAck, OrderRequest
from kairospy.integrations.ports import Environment
from kairospy.runtime.clock import FixedClock
from kairospy.runtime import ManagedServiceSpec
from kairospy.execution.orders import OrderType
from kairospy.execution.events import TradeSide
from kairospy.identity import AccountRef, AccountType, InstrumentId, VenueId
from kairospy.execution.orders import ExecutionInstructions, TimeInForce
from kairospy.execution.command import OutboxStatus
from kairospy.execution.order_state import DurableOrderStatus
from kairospy.execution.outbox import DurableOrderCommandService, DurableOrderDispatcher, DurableOrderDispatcherService
from kairospy.governance.kill_switch import KillSwitch
from kairospy.runtime.application import KairosApplication
from kairospy.runtime.async_runtime import AsyncKairosRuntime
from kairospy.runtime.config import ApplicationConfig, RuntimePaths
from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore


NOW = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)


def request() -> OrderRequest:
    return OrderRequest(
        "internal-1", "client-1", "strategy-v1", "intent-1", "correlation-1",
        AccountRef(InstitutionId("simulated"), "account-1", AccountType.SECURITIES_MARGIN),
        InstrumentId("instrument-1"), TradeSide.BUY, Decimal("1"),
        ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("10")),
    )


def reduce_only_request() -> OrderRequest:
    original = request()
    return OrderRequest(
        original.internal_order_id,
        "client-reduce-only",
        original.strategy_id,
        original.intent_id,
        original.correlation_id,
        original.account,
        original.instrument_id,
        original.side,
        original.quantity,
        ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("10"), reduce_only=True),
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
            self.assertEqual(dispatcher.last_metrics["outbox_pending_count"], 0)
            self.assertEqual(dispatcher.last_metrics["outbox_backlog_count"], 0)
            self.assertEqual(dispatcher.last_metrics["order_submit_latency_last_ms"], 0.0)
            self.assertEqual(dispatcher.last_metrics["order_ack_latency_last_ms"], 0.0)

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
            application = KairosApplication(
                ApplicationConfig(Environment.PAPER, paths), store, runtime_id="outbox-runtime-fixture",
            )
            service = DurableOrderDispatcherService(
                store,
                dispatcher,
                run_id="outbox-runtime-fixture",
                idle_wait_seconds=0.001,
                clock=FixedClock(NOW),
            )
            runtime = AsyncKairosRuntime(application, (
                service.managed_service(),
            ))

            await runtime.start()
            for _ in range(100):
                if store.outbox_commands()[0].status is OutboxStatus.COMPLETED:
                    break
                await asyncio.sleep(0)

            self.assertEqual(store.outbox_commands()[0].status, OutboxStatus.COMPLETED)
            self.assertEqual(store.order("client-1").status, DurableOrderStatus.ACKNOWLEDGED)  # type: ignore[union-attr]
            await runtime.stop()
            state = store.runtime_state(service.state_key)
            assert isinstance(state, dict)
            self.assertEqual(state["phase"], "stopped")
            self.assertEqual(state["outbox_pending_count"], 0)
            self.assertEqual(state["outbox_backlog_count"], 0)
            self.assertEqual(state["order_submit_latency_last_ms"], 0.0)
            self.assertEqual(state["order_ack_latency_last_ms"], 0.0)

    async def test_command_service_enforces_application_and_kill_switch_gates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = RuntimePaths.under(root)
            store = SQLiteRuntimeStore(paths.runtime_database)
            application = KairosApplication(
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

    async def test_reconciliation_mismatch_blocks_non_reducing_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = RuntimePaths.under(root)
            store = SQLiteRuntimeStore(paths.runtime_database)
            application = KairosApplication(
                ApplicationConfig(Environment.PAPER, paths), store, runtime_id="command-service-reconciliation",
            )
            service = DurableOrderCommandService(
                store,
                application,
                KillSwitch((), FixedClock(NOW), store),
                lambda _request: None,
                clock=FixedClock(NOW),
            )
            application.start()
            application.run()
            store.set_runtime_state("reconciliation:last", {"matched": False, "phase": "mismatched"}, NOW)

            with self.assertRaisesRegex(RuntimeError, "reconciliation mismatch"):
                service.submit(request())

            accepted = service.submit(reduce_only_request())

            self.assertEqual(accepted.status, OutboxStatus.PENDING)
            application.stop()

    async def test_risk_runtime_state_blocks_non_reducing_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = RuntimePaths.under(root)
            store = SQLiteRuntimeStore(paths.runtime_database)
            application = KairosApplication(
                ApplicationConfig(Environment.PAPER, paths), store, runtime_id="command-service-risk",
            )
            service = DurableOrderCommandService(
                store,
                application,
                KillSwitch((), FixedClock(NOW), store),
                lambda _request: None,
                clock=FixedClock(NOW),
            )
            application.start()
            application.run()
            store.set_runtime_state("risk_runtime:last", {"status": "stale", "limits_hash": "old"}, NOW)

            with self.assertRaisesRegex(RuntimeError, "risk runtime"):
                service.submit(request())

            accepted = service.submit(reduce_only_request())

            self.assertEqual(accepted.status, OutboxStatus.PENDING)
            application.stop()


if __name__ == "__main__":
    unittest.main()
