from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from trading.accounting.ledger import LedgerService
from trading.adapters.base import OrderAck
from trading.adapters.simulated import SimulatedExecutionAccountAdapter
from trading.application import ApplicationConfig, FixedClock, RuntimePaths, RuntimeRecoveryService, RuntimeStatus, TradingApplication
from trading.application.runtime_failure_matrix import run_runtime_failure_matrix
from trading.domain.execution import TradeExecution, TradeSide
from trading.domain.identity import AssetId, VenueId
from trading.domain.ledger import Ledger
from trading.execution.ingestion import DurableExecutionIngestionService
from trading.execution.order_state import DurableOrderStatus
from trading.execution.recovery import VenueOrderRecoveryService
from trading.execution.router import ExecutionRouter
from trading.orchestration.coordinator import TradingCoordinator
from trading.orchestration.event_log import PersistentEventLog
from trading.orchestration.faults import InjectedRuntimeFailure, OneShotRuntimeFaultInjector, RuntimeFaultPoint
from trading.orchestration.kill_switch import KillSwitch
from trading.orchestration.reconciliation import ReconciliationService
from trading.orchestration.runtime_store import SQLiteRuntimeStore
from tests.test_durable_execution_ingestion import catalog
from tests.test_runtime_store import request
from tests.runtime_support import operational_application


NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


def acknowledged(store: SQLiteRuntimeStore):
    order = request()
    store.create_order(order, NOW)
    store.transition_order(order.client_order_id, DurableOrderStatus.APPROVED, NOW)
    store.transition_order(order.client_order_id, DurableOrderStatus.SUBMITTING, NOW)
    store.transition_order(order.client_order_id, DurableOrderStatus.ACKNOWLEDGED, NOW, ack=OrderAck(
        order.internal_order_id, order.client_order_id, order.strategy_id, order.intent_id,
        order.correlation_id, "venue-order-1", NOW,
    ))
    return order


def execution(order, suffix: int, *, quantity: str = "1") -> TradeExecution:
    return TradeExecution(
        UUID(f"00000000-0000-0000-0000-{suffix:012d}"), NOW + timedelta(seconds=suffix),
        order.account, order.instrument_id, TradeSide.BUY, Decimal(quantity), Decimal("10"),
        AssetId("USDT"), Decimal("0.1"), order.client_order_id,
    )


class RuntimeFailureMatrixTests(unittest.TestCase):
    def test_complete_matrix_has_fixed_audit_hash_and_is_rerunnable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = run_runtime_failure_matrix(directory)
            second = run_runtime_failure_matrix(directory)
            self.assertTrue(first["passed"])
            self.assertEqual(first["audit_hash"], "bf74045ae8c728dc55518815bfcd717b19f60d68e1c2d9141e202e121096a917")
            self.assertEqual(second["audit_hash"], first["audit_hash"])
            self.assertEqual(len(first["cases"]), 9)

    def _coordinator(self, directory: str, store: SQLiteRuntimeStore, adapter, injector=None):
        ledger = store.load_ledger()
        coordinator = TradingCoordinator(
            ExecutionRouter(catalog(), (adapter,)),
            {request().account: ReconciliationService(ledger, adapter, clock=FixedClock(NOW))},
            KillSwitch((adapter,), FixedClock(NOW), store),
            PersistentEventLog(Path(directory) / "events.jsonl"),
            clock=FixedClock(NOW), runtime_store=store, fault_injector=injector,
            application=operational_application(directory, store, clock=FixedClock(NOW)),
        )
        return coordinator

    def test_crash_before_venue_call_never_submits_and_remains_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            adapter = SimulatedExecutionAccountAdapter(VenueId("simulated"), request().account, clock=FixedClock(NOW))
            coordinator = self._coordinator(directory, store, adapter, OneShotRuntimeFaultInjector(
                RuntimeFaultPoint.AFTER_ORDER_SUBMITTING_BEFORE_VENUE,
            ))
            with self.assertRaises(InjectedRuntimeFailure):
                coordinator.submit(request(), NOW)
            self.assertEqual(store.order(request().client_order_id).status, DurableOrderStatus.SUBMITTING)  # type: ignore[union-attr]
            self.assertEqual(adapter.orders, {})
            with self.assertRaisesRegex(RuntimeError, "venue resolution"):
                self._coordinator(directory, store, adapter)
            self.assertEqual(adapter.orders, {})
            self.assertEqual(store.load_ledger().transactions, ())

    def test_venue_accept_before_ack_persistence_recovers_without_resubmission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            adapter = SimulatedExecutionAccountAdapter(VenueId("simulated"), request().account, clock=FixedClock(NOW))
            coordinator = self._coordinator(directory, store, adapter, OneShotRuntimeFaultInjector(
                RuntimeFaultPoint.AFTER_VENUE_ACCEPT_BEFORE_ACK_PERSIST,
            ))
            with self.assertRaises(InjectedRuntimeFailure):
                coordinator.submit(request(), NOW)
            self.assertEqual(len(adapter.orders), 1)
            recovery = VenueOrderRecoveryService(
                store, {request().account: adapter},
                DurableExecutionIngestionService(LedgerService(store.load_ledger(), catalog()), store),
            )
            report = recovery.recover(NOW + timedelta(seconds=1))
            self.assertEqual(report.unresolved, ())
            self.assertEqual(store.order(request().client_order_id).status, DurableOrderStatus.ACKNOWLEDGED)  # type: ignore[union-attr]
            ack = self._coordinator(directory, store, adapter).submit(request(), NOW)
            self.assertEqual(ack.client_order_id, request().client_order_id)
            self.assertEqual(len(adapter.orders), 1)

    def test_partial_fill_crash_rebuilds_exactly_one_ledger_fact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.sqlite3"
            store = SQLiteRuntimeStore(path)
            order = acknowledged(store)
            fill = execution(order, 1, quantity="0.4")
            DurableExecutionIngestionService(LedgerService(Ledger(), catalog()), store).ingest(
                fill, external_key="partial-1", client_order_id=order.client_order_id,
                fully_filled=False, cursor_name="fills", cursor_value="1",
            )
            restarted = SQLiteRuntimeStore(path)
            self.assertEqual(restarted.order(order.client_order_id).status, DurableOrderStatus.PARTIALLY_FILLED)  # type: ignore[union-attr]
            self.assertEqual(len(restarted.load_ledger().transactions), 1)
            duplicate = DurableExecutionIngestionService(
                LedgerService(restarted.load_ledger(), catalog()), restarted,
            ).ingest(fill, external_key="partial-1", client_order_id=order.client_order_id, fully_filled=False)
            self.assertIsNone(duplicate)
            self.assertEqual(len(restarted.load_ledger().transactions), 1)

    def test_rest_websocket_duplicate_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            order = acknowledged(store)
            fill = execution(order, 2)
            ingestion = DurableExecutionIngestionService(LedgerService(Ledger(), catalog()), store)
            self.assertIsNotNone(ingestion.ingest(
                fill, external_key="venue:trade:2", client_order_id=order.client_order_id, fully_filled=True,
            ))
            self.assertIsNone(ingestion.ingest(
                fill, external_key="venue:trade:2", client_order_id=order.client_order_id, fully_filled=True,
            ))
            self.assertEqual(len(store.load_ledger().transactions), 1)

    def test_ledger_transaction_interruption_rolls_back_every_related_fact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.sqlite3"
            injector = OneShotRuntimeFaultInjector(RuntimeFaultPoint.DURING_EXECUTION_TRANSACTION)
            store = SQLiteRuntimeStore(path, fault_injector=injector)
            order = acknowledged(store)
            fill = execution(order, 3)
            with self.assertRaises(InjectedRuntimeFailure):
                DurableExecutionIngestionService(LedgerService(Ledger(), catalog()), store).ingest(
                    fill, external_key="atomic-3", client_order_id=order.client_order_id,
                    fully_filled=True, cursor_name="fills", cursor_value="3",
                )
            restarted = SQLiteRuntimeStore(path)
            self.assertEqual(restarted.order(order.client_order_id).status, DurableOrderStatus.ACKNOWLEDGED)  # type: ignore[union-attr]
            self.assertEqual(restarted.load_ledger().transactions, ())
            self.assertIsNone(restarted.cursor("fills"))
            self.assertIsNotNone(DurableExecutionIngestionService(
                LedgerService(Ledger(), catalog()), restarted,
            ).ingest(fill, external_key="atomic-3", client_order_id=order.client_order_id, fully_filled=True))
            self.assertEqual(len(restarted.load_ledger().transactions), 1)

    def test_kill_switch_restart_keeps_non_reducing_orders_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.sqlite3"
            store = SQLiteRuntimeStore(path)
            adapter = SimulatedExecutionAccountAdapter(VenueId("simulated"), request().account, clock=FixedClock(NOW))
            KillSwitch((adapter,), FixedClock(NOW), store).trigger((), "failure matrix drill")
            restarted = KillSwitch((adapter,), FixedClock(NOW), SQLiteRuntimeStore(path))
            self.assertTrue(restarted.triggered)
            coordinator = TradingCoordinator(
                ExecutionRouter(catalog(), (adapter,)),
                {request().account: ReconciliationService(Ledger(), adapter, clock=FixedClock(NOW))},
                restarted, PersistentEventLog(Path(directory) / "events.jsonl"),
                runtime_store=store,
                application=operational_application(directory, store, clock=FixedClock(NOW)),
            )
            with self.assertRaisesRegex(RuntimeError, "only reduce-only"):
                coordinator.submit(request(), NOW)
            self.assertEqual(adapter.orders, {})

    def test_reconciliation_mismatch_blocks_application_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths.under(directory)
            store = SQLiteRuntimeStore(paths.runtime_database)
            adapter = SimulatedExecutionAccountAdapter(
                VenueId("simulated"), request().account,
                balances=((AssetId("USDT"), Decimal("1")),), clock=FixedClock(NOW),
            )
            app = TradingApplication(
                ApplicationConfig(adapter.environment, paths), store, runtime_id="mismatch",
                accounts=(request().account,), clock=FixedClock(NOW),
                recovery=RuntimeRecoveryService(store, catalog(), AssetId("USDT"), {request().account: adapter}),
            )
            with self.assertRaisesRegex(RuntimeError, "reconciliation mismatches"):
                app.start()
            self.assertEqual(app.status, RuntimeStatus.UNKNOWN_EXTERNAL_STATE)
            app.stop()

    def test_expired_account_lock_takeover_rejects_old_owner_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            account = request().account
            store.acquire_account_lock(account, "old", NOW, lease_seconds=5)
            store.acquire_account_lock(account, "new", NOW + timedelta(seconds=6), lease_seconds=5)
            with self.assertRaisesRegex(RuntimeError, "no longer owns"):
                store.heartbeat_account_lock(account, "old", NOW + timedelta(seconds=7), lease_seconds=5)


if __name__ == "__main__":
    unittest.main()
