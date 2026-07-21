from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from kairospy.accounting.ledger import LedgerService
from kairospy.ports import (
    Environment, OrderAck, RecoveredExecution, VenueOrderRecovery, VenueOrderStatus,
)
from kairospy.connectors.simulated import SimulatedExecutionAccountGateway
from kairospy.trading.execution import TradeExecution, TradeSide
from kairospy.trading.identity import AssetId, VenueId
from kairospy.trading.ledger import Ledger
from kairospy.execution.ingestion import DurableExecutionIngestionService
from kairospy.execution.order_state import DurableOrderStatus
from kairospy.execution.recovery import VenueOrderRecoveryService
from kairospy.orchestration.runtime_store import SQLiteRuntimeStore
from tests.test_durable_execution_ingestion import catalog
from tests.test_runtime_store import request


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class FilledRecoveryGateway:
    venue_id = VenueId("simulated")
    environment = Environment.TESTNET

    def __init__(self, outcome: VenueOrderRecovery) -> None:
        self.outcome = outcome

    def recover_order(self, account, request, venue_order_id=None):
        return self.outcome


class VenueOrderRecoveryTests(unittest.TestCase):
    def test_submitting_crash_window_is_resolved_by_client_id_without_resubmission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            order = request()
            gateway = SimulatedExecutionAccountGateway(VenueId("simulated"), order.account)
            venue_ack = gateway.place_order(order)
            store.create_order(order, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.APPROVED, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.SUBMITTING, NOW)
            service = VenueOrderRecoveryService(
                store,
                {order.account: gateway},
                DurableExecutionIngestionService(LedgerService(Ledger(), catalog()), store),
            )

            report = service.recover(NOW + timedelta(seconds=1))

            self.assertTrue(report.complete)
            recovered = store.order(order.client_order_id)
            assert recovered is not None
            self.assertEqual(recovered.status, DurableOrderStatus.ACKNOWLEDGED)
            self.assertEqual(recovered.ack, venue_ack)
            self.assertEqual(len(gateway.orders), 1)

    def test_unknown_filled_order_recovers_execution_ledger_and_cursor_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            order = request()
            store.create_order(order, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.APPROVED, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.SUBMITTING, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.UNKNOWN, NOW, reason="ack timeout")
            execution = TradeExecution(
                UUID("00000000-0000-0000-0000-000000000401"),
                NOW + timedelta(seconds=1),
                order.account,
                order.instrument_id,
                TradeSide.BUY,
                Decimal("1"),
                Decimal("10"),
                AssetId("USDT"),
                Decimal("0.1"),
                order.client_order_id,
            )
            outcome = VenueOrderRecovery(
                VenueOrderStatus.FILLED,
                "REST order status plus trade history",
                acknowledgement=OrderAck(
                    order.internal_order_id,
                    order.client_order_id,
                    order.strategy_id,
                    order.intent_id,
                    order.correlation_id,
                    "venue-order-1",
                    NOW,
                ),
                executions=(RecoveredExecution(
                    "simulated:recovered-fill-1",
                    execution,
                    True,
                    "simulated:fills",
                    "401",
                ),),
            )
            ledger = Ledger()
            service = VenueOrderRecoveryService(
                store,
                {order.account: FilledRecoveryGateway(outcome)},
                DurableExecutionIngestionService(LedgerService(ledger, catalog()), store),
            )

            report = service.recover(NOW + timedelta(seconds=2))

            self.assertTrue(report.complete)
            self.assertEqual(store.order(order.client_order_id).status, DurableOrderStatus.FILLED)  # type: ignore[union-attr]
            self.assertEqual(store.cursor("simulated:fills"), "401")
            self.assertEqual(len(store.load_ledger().transactions), 1)
            self.assertEqual(len(ledger.transactions), 1)

    def test_unknown_without_venue_proof_remains_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            order = request()
            store.create_order(order, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.APPROVED, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.SUBMITTING, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.UNKNOWN, NOW)
            gateway = SimulatedExecutionAccountGateway(VenueId("simulated"), order.account)
            report = VenueOrderRecoveryService(
                store,
                {order.account: gateway},
                DurableExecutionIngestionService(LedgerService(Ledger(), catalog()), store),
            ).recover(NOW + timedelta(seconds=1))
            self.assertFalse(report.complete)
            self.assertEqual(report.unresolved, (order.client_order_id,))
            self.assertEqual(store.order(order.client_order_id).status, DurableOrderStatus.UNKNOWN)  # type: ignore[union-attr]

    def test_acknowledged_order_backfills_fill_missed_during_disconnect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            order = request()
            store.create_order(order, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.APPROVED, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.SUBMITTING, NOW)
            ack = OrderAck(
                order.internal_order_id, order.client_order_id, order.strategy_id, order.intent_id,
                order.correlation_id, "venue-working-1", NOW,
            )
            store.transition_order(order.client_order_id, DurableOrderStatus.ACKNOWLEDGED, NOW, ack=ack)
            fill = TradeExecution(
                UUID("00000000-0000-0000-0000-000000000402"), NOW + timedelta(seconds=1),
                order.account, order.instrument_id, TradeSide.BUY, Decimal("1"), Decimal("10"),
                AssetId("USDT"), Decimal("0.1"), order.client_order_id,
            )
            outcome = VenueOrderRecovery(
                VenueOrderStatus.FILLED, "REST backfill after stream disconnect", acknowledgement=ack,
                executions=(RecoveredExecution(
                    "simulated:disconnect-fill-402", fill, True,
                    "simulated:reconnect-fills", "402",
                ),),
            )
            report = VenueOrderRecoveryService(
                store, {order.account: FilledRecoveryGateway(outcome)},
                DurableExecutionIngestionService(LedgerService(Ledger(), catalog()), store),
            ).recover(NOW + timedelta(seconds=2))
            self.assertTrue(report.complete)
            self.assertEqual(store.order(order.client_order_id).status, DurableOrderStatus.FILLED)  # type: ignore[union-attr]
            self.assertEqual(store.cursor("simulated:reconnect-fills"), "402")
            self.assertEqual(len(store.load_ledger().transactions), 1)

    def test_venue_cancel_and_expire_are_durably_recovered(self) -> None:
        for venue_status, durable_status in (
            (VenueOrderStatus.CANCELLED, DurableOrderStatus.CANCELLED),
            (VenueOrderStatus.EXPIRED, DurableOrderStatus.EXPIRED),
        ):
            with self.subTest(status=venue_status), tempfile.TemporaryDirectory() as directory:
                store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
                order = request()
                store.create_order(order, NOW)
                store.transition_order(order.client_order_id, DurableOrderStatus.APPROVED, NOW)
                store.transition_order(order.client_order_id, DurableOrderStatus.SUBMITTING, NOW)
                ack = OrderAck(
                    order.internal_order_id, order.client_order_id, order.strategy_id, order.intent_id,
                    order.correlation_id, "venue-terminal-1", NOW,
                )
                store.transition_order(order.client_order_id, DurableOrderStatus.ACKNOWLEDGED, NOW, ack=ack)
                report = VenueOrderRecoveryService(
                    store, {order.account: FilledRecoveryGateway(VenueOrderRecovery(
                        venue_status, f"venue reported {venue_status.value}", acknowledgement=ack,
                    ))},
                    DurableExecutionIngestionService(LedgerService(Ledger(), catalog()), store),
                ).recover(NOW + timedelta(seconds=1))
                self.assertTrue(report.complete)
                self.assertEqual(store.order(order.client_order_id).status, durable_status)  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
