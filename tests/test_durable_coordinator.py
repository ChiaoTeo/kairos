from __future__ import annotations

from kairospy.identity import InstitutionId

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from kairospy.integrations.ports import ComboLegRequest, ComboOrderRequest, OrderAck, OrderRequest
from kairospy.runtime.clock import FixedClock
from kairospy.execution.orders import OrderType
from kairospy.execution.events import TradeSide
from kairospy.identity import AccountRef, AccountType, InstrumentId, VenueId
from kairospy.reference.contracts import ProductType
from kairospy.runtime.application import RuntimeStatus
from kairospy.runtime.stop_controller import RuntimeStopController
from kairospy.strategy.contracts import StrategyLifecycle, StrategySpec
from kairospy.strategy.intents import CancelIntent
from kairospy.strategy.stop_policy import StopAction, StopPolicy, StopReason, StopRule
from kairospy.execution.orders import ExecutionInstructions, TimeInForce
from kairospy.execution.order_state import DurableOrderStatus
from kairospy.runtime.coordinator import ExecutionCoordinator
from kairospy.runtime.store.event_log import PersistentEventLog
from kairospy.governance.kill_switch import KillSwitch
from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore
from tests.runtime_support import operational_application


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def order_request(*, client_id: str = "client-1", strategy_id: str = "strategy-v1") -> OrderRequest:
    return OrderRequest(
        f"internal-{client_id}", client_id, strategy_id, "intent-1", "correlation-1",
        AccountRef(InstitutionId("simulated"), "account-1", AccountType.SECURITIES_MARGIN),
        InstrumentId("instrument-1"), TradeSide.BUY, Decimal("1"),
        ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("10")),
    )


def strategy_spec(strategy_id: str = "strategy-v1") -> StrategySpec:
    return StrategySpec(
        strategy_id,
        "1.0.0",
        StrategyLifecycle.DRAFT,
        (ProductType.CRYPTO_SPOT,),
        ("target_position",),
        ("momentum",),
        ("price",),
        (("instrument", "instrument-1"),),
        ("price",),
        (("threshold", "0"),),
        (("target", "position"),),
        ("enter",),
        ("exit",),
        ("manual",),
        Decimal("0.01"),
        ("bars",),
        ("limit_orders",),
        "evidence-hash",
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
            first = ExecutionCoordinator(
                router, {}, KillSwitch(()), PersistentEventLog(root / "events.jsonl"),
                FixedClock(NOW), store,
                application=operational_application(root, store, clock=FixedClock(NOW)),
            )
            ack = first.submit(order_request(), NOW)
            self.assertEqual(router.submissions, 1)

            restarted_router = RecordingRouter()
            restarted_store = SQLiteRuntimeStore(root / "runtime.sqlite3")
            restarted = ExecutionCoordinator(
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
            coordinator = ExecutionCoordinator(
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
            coordinator = ExecutionCoordinator(
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

    def test_strategy_scoped_cancellation_only_cancels_matching_working_orders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SQLiteRuntimeStore(root / "runtime.sqlite3")
            router = RecordingRouter()
            coordinator = ExecutionCoordinator(
                router, {}, KillSwitch(()), PersistentEventLog(root / "events.jsonl"),
                FixedClock(NOW), store,
                application=operational_application(root, store, clock=FixedClock(NOW)),
            )
            target = order_request(client_id="target-client", strategy_id="strategy-v1")
            other = order_request(client_id="other-client", strategy_id="strategy-v2")
            coordinator.submit(target, NOW)
            coordinator.submit(other, NOW)

            working = store.strategy_working_orders("strategy-v1")
            self.assertEqual(tuple(item.request.client_order_id for item in working), ("target-client",))
            result = coordinator.cancel_strategy_orders("strategy-v1", target.account, "operator stop")

            self.assertEqual(result.cancelled_client_order_ids, ("target-client",))
            self.assertEqual(result.failures, ())
            self.assertEqual(store.order("target-client").status, DurableOrderStatus.CANCELLED)  # type: ignore[union-attr]
            self.assertEqual(store.order("other-client").status, DurableOrderStatus.ACKNOWLEDGED)  # type: ignore[union-attr]
            self.assertEqual(router.cancellations, ["venue-1"])

    def test_runtime_stop_controller_applies_reduce_only_cancels_and_persists_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SQLiteRuntimeStore(root / "runtime.sqlite3")
            router = RecordingRouter()
            application = operational_application(root, store, clock=FixedClock(NOW))
            coordinator = ExecutionCoordinator(
                router, {}, KillSwitch(()), PersistentEventLog(root / "events.jsonl"),
                FixedClock(NOW), store,
                application=application,
            )
            order = order_request()
            coordinator.submit(order, NOW)

            report = RuntimeStopController(
                application,
                coordinator,
                strategy_spec(order.strategy_id),
                accounts=(order.account,),
                clock=FixedClock(NOW),
            ).execute(StopReason.RISK_BREACH)

            self.assertEqual(report.requested_action, StopAction.REDUCE_ONLY)
            self.assertEqual(report.action, StopAction.REDUCE_ONLY)
            self.assertTrue(report.reduce_only_applied)
            self.assertEqual(report.cancelled_client_order_ids, (order.client_order_id,))
            self.assertEqual(application.status, RuntimeStatus.REDUCE_ONLY)
            self.assertEqual(store.order(order.client_order_id).status, DurableOrderStatus.CANCELLED)  # type: ignore[union-attr]
            state = store.runtime_state(f"runtime_stop:{order.strategy_id}:risk_breach")
            assert isinstance(state, dict)
            self.assertEqual(state["action"], "reduce_only")
            self.assertEqual(state["cancelled_client_order_ids"], [order.client_order_id])
            last = store.runtime_state("runtime_stop:last")
            assert isinstance(last, dict)
            self.assertEqual(last["strategy_id"], order.strategy_id)
            self.assertEqual(last["reason"], "risk_breach")

    def test_runtime_stop_controller_reports_flatten_manual_approval_when_downgraded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SQLiteRuntimeStore(root / "runtime.sqlite3")
            application = operational_application(root, store, clock=FixedClock(NOW))
            coordinator = ExecutionCoordinator(
                RecordingRouter(), {}, KillSwitch(()), PersistentEventLog(root / "events.jsonl"),
                FixedClock(NOW), store,
                application=application,
            )
            strategy = replace(
                strategy_spec("strategy-v1"),
                default_stop_policy=StopPolicy((
                    StopRule(StopReason.MANUAL, StopAction.FLATTEN),
                )),
            )

            report = RuntimeStopController(
                application,
                coordinator,
                strategy,
                accounts=(),
                clock=FixedClock(NOW),
            ).execute(StopReason.MANUAL)

            self.assertEqual(report.requested_action, StopAction.FLATTEN)
            self.assertEqual(report.action, StopAction.REDUCE_ONLY)
            self.assertTrue(report.flatten_requires_manual_approval)
            self.assertTrue(report.reduce_only_applied)
            last = store.runtime_state("runtime_stop:last")
            assert isinstance(last, dict)
            self.assertTrue(last["flatten_requires_manual_approval"])


if __name__ == "__main__":
    unittest.main()
