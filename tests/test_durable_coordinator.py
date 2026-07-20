from __future__ import annotations

from kairos.domain.identity import InstitutionId

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from kairos.ports import ComboLegRequest, ComboOrderRequest, OrderAck, OrderRequest
from kairos.application.clock import FixedClock
from kairos.domain.capability import OrderType
from kairos.domain.execution import TradeSide
from kairos.domain.identity import AccountKey, AccountType, InstrumentId, VenueId
from kairos.domain.intent import CancelIntent
from kairos.domain.order import ExecutionInstructions, TimeInForce
from kairos.execution.order_state import DurableOrderStatus
from kairos.orchestration.coordinator import TradingCoordinator
from kairos.orchestration.event_log import PersistentEventLog
from kairos.orchestration.kill_switch import KillSwitch
from kairos.orchestration.runtime_store import SQLiteRuntimeStore
from tests.runtime_support import operational_application


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def order_request() -> OrderRequest:
    return OrderRequest(
        "internal-1", "client-1", "strategy-v1", "intent-1", "correlation-1",
        AccountKey(InstitutionId("simulated"), "account-1", AccountType.SECURITIES_MARGIN),
        InstrumentId("instrument-1"), TradeSide.BUY, Decimal("1"),
        ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("10")),
    )


class RecordingRouter:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.submissions = 0
        self.cancellations: list[str] = []

    def submit(self, request: OrderRequest, at: datetime) -> OrderAck:
        self.submissions += 1
        if self.failure is not None:
            raise self.failure
        return OrderAck(
            request.internal_order_id, request.client_order_id, request.strategy_id,
            request.intent_id, request.correlation_id, "venue-1", at,
        )

    def submit_combo(self, request: ComboOrderRequest, at: datetime) -> OrderAck:
        self.submissions += 1
        if self.failure is not None:
            raise self.failure
        return OrderAck(
            request.internal_order_id, request.client_order_id, request.strategy_id,
            request.intent_id, request.correlation_id, "combo-venue-1", at,
        )

    def cancel(self, account, venue_order_id: str) -> None:
        self.cancellations.append(venue_order_id)


class DurableCoordinatorTests(unittest.TestCase):
    def test_acknowledged_order_is_recovered_without_resubmission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SQLiteRuntimeStore(root / "runtime.sqlite3")
            router = RecordingRouter()
            first = TradingCoordinator(
                router, {}, KillSwitch(()), PersistentEventLog(root / "events.jsonl"),
                FixedClock(NOW), store,
                application=operational_application(root, store, clock=FixedClock(NOW)),
            )
            ack = first.submit(order_request(), NOW)
            self.assertEqual(router.submissions, 1)

            restarted_router = RecordingRouter()
            restarted_store = SQLiteRuntimeStore(root / "runtime.sqlite3")
            restarted = TradingCoordinator(
                restarted_router, {}, KillSwitch(()), PersistentEventLog(root / "events.jsonl"),
                FixedClock(NOW), restarted_store,
                application=operational_application(root, restarted_store, clock=FixedClock(NOW)),
            )
            self.assertEqual(restarted.submit(order_request(), NOW), ack)
            self.assertEqual(restarted_router.submissions, 0)

    def test_ambiguous_external_failure_is_persisted_and_fails_closed_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SQLiteRuntimeStore(root / "runtime.sqlite3")
            router = RecordingRouter(failure=ConnectionError("connection lost after submit"))
            coordinator = TradingCoordinator(
                router, {}, KillSwitch(()), PersistentEventLog(root / "events.jsonl"),
                FixedClock(NOW), store,
                application=operational_application(root, store, clock=FixedClock(NOW)),
            )
            with self.assertRaises(ConnectionError):
                coordinator.submit(order_request(), NOW)
            record = store.order("client-1")
            assert record is not None
            self.assertEqual(record.status, DurableOrderStatus.UNKNOWN)

            restarted_router = RecordingRouter()
            restarted_store = SQLiteRuntimeStore(root / "runtime.sqlite3")
            with self.assertRaisesRegex(RuntimeError, "venue resolution"):
                operational_application(root, restarted_store, clock=FixedClock(NOW))
            self.assertEqual(restarted_router.submissions, 0)

    def test_combo_and_cancel_share_the_durable_order_state_machine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SQLiteRuntimeStore(root / "runtime.sqlite3")
            router = RecordingRouter()
            coordinator = TradingCoordinator(
                router, {}, KillSwitch(()), PersistentEventLog(root / "events.jsonl"),
                FixedClock(NOW), store,
                application=operational_application(root, store, clock=FixedClock(NOW)),
            )
            base = order_request()
            combo = ComboOrderRequest(
                "combo-internal", "combo-client", base.strategy_id, base.intent_id,
                base.correlation_id, base.account,
                (ComboLegRequest(base.instrument_id, TradeSide.BUY, 1),), Decimal("1"), base.instructions,
            )
            ack = coordinator.submit_combo(combo, NOW)
            stored = store.order(combo.client_order_id)
            assert stored is not None
            self.assertEqual(stored.request, combo)
            self.assertEqual(stored.status, DurableOrderStatus.ACKNOWLEDGED)
            coordinator.cancel(
                CancelIntent(
                    UUID("00000000-0000-0000-0000-000000000199"), base.strategy_id,
                    combo.client_order_id, "operator cancel",
                ),
                base.account,
            )
            cancelled = store.order(combo.client_order_id)
            assert cancelled is not None
            self.assertEqual(cancelled.status, DurableOrderStatus.CANCELLED)
            self.assertEqual(router.cancellations, [ack.venue_order_id])


if __name__ == "__main__":
    unittest.main()
