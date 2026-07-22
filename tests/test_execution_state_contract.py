from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from kairospy.execution.command import OutboxStatus
from kairospy.execution.events import TradeSide
from kairospy.execution.order_state import (
    ALLOWED_ORDER_TRANSITIONS,
    DurableOrderStatus,
    require_order_transition,
)
from kairospy.execution.orders import ExecutionInstructions, OrderType, TimeInForce
from kairospy.identity import AccountRef, AccountType, InstitutionId, InstrumentId
from kairospy.integrations.ports import OrderRequest
from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore
from kairospy.strategy.views import IntentView, OrderView


NOW = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)


class ExecutionStateContractTests(unittest.TestCase):
    def test_execution_owner_defines_total_durable_order_transition_contract(self) -> None:
        self.assertEqual(set(ALLOWED_ORDER_TRANSITIONS), set(DurableOrderStatus))

        require_order_transition(DurableOrderStatus.PLANNED, DurableOrderStatus.APPROVED)
        require_order_transition(DurableOrderStatus.APPROVED, DurableOrderStatus.SUBMITTING)
        require_order_transition(DurableOrderStatus.SUBMITTING, DurableOrderStatus.UNKNOWN)
        require_order_transition(DurableOrderStatus.UNKNOWN, DurableOrderStatus.ACKNOWLEDGED)
        require_order_transition(DurableOrderStatus.ACKNOWLEDGED, DurableOrderStatus.PARTIALLY_FILLED)
        require_order_transition(DurableOrderStatus.PARTIALLY_FILLED, DurableOrderStatus.FILLED)

        for terminal in (
            DurableOrderStatus.REJECTED,
            DurableOrderStatus.FILLED,
            DurableOrderStatus.CANCELLED,
            DurableOrderStatus.EXPIRED,
        ):
            self.assertTrue(terminal.terminal)
            self.assertEqual(ALLOWED_ORDER_TRANSITIONS[terminal], frozenset())

        illegal = (
            (DurableOrderStatus.PLANNED, DurableOrderStatus.FILLED),
            (DurableOrderStatus.ACKNOWLEDGED, DurableOrderStatus.APPROVED),
            (DurableOrderStatus.FILLED, DurableOrderStatus.UNKNOWN),
        )
        for current, target in illegal:
            with self.subTest(current=current, target=target):
                with self.assertRaisesRegex(ValueError, "illegal durable order transition"):
                    require_order_transition(current, target)

    def test_runtime_store_cannot_bypass_execution_transition_contract_for_views(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            command = store.enqueue_order_command(_request(), NOW)

            with self.assertRaisesRegex(ValueError, "illegal durable order transition"):
                store.transition_order("client-1", DurableOrderStatus.FILLED, NOW)

            order = store.order("client-1")
            assert order is not None
            self.assertEqual(order.status, DurableOrderStatus.PLANNED)
            self.assertEqual(store.outbox_commands()[0].status, OutboxStatus.PENDING)

            order_view = OrderView.from_execution_state(
                orders=(order,),
                outbox_records=(command,),
            )
            intent_view = IntentView.from_executions(
                (),
                orders=(order,),
                outbox_records=(command,),
            )

            self.assertEqual(order_view.working[0].status, DurableOrderStatus.PLANNED.value)
            self.assertEqual(order_view.working[0].command_status, OutboxStatus.PENDING.value)
            self.assertEqual(intent_view.executions[0].order_states, (("client-1", DurableOrderStatus.PLANNED.value),))
            self.assertEqual(intent_view.executions[0].command_ids, ("submit:client-1",))


def _request() -> OrderRequest:
    return OrderRequest(
        "internal-1",
        "client-1",
        "strategy-v1",
        "intent-1",
        "correlation-1",
        AccountRef(InstitutionId("simulated"), "account-1", AccountType.SECURITIES_MARGIN),
        InstrumentId("instrument-1"),
        TradeSide.BUY,
        Decimal("1"),
        ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("10")),
    )


if __name__ == "__main__":
    unittest.main()
