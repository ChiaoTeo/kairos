from __future__ import annotations

from kairospy.identity import InstitutionId

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from kairospy.portfolio.accounting.ledger import LedgerService
from kairospy.integrations.ports import OrderAck, OrderRequest
from kairospy.execution.orders import OrderType
from kairospy.execution.events import TradeExecution, TradeSide
from kairospy.identity import AccountRef, AccountType, AssetId, InstrumentId, VenueId
from kairospy.portfolio.ledger import Ledger
from kairospy.execution.orders import ExecutionInstructions, TimeInForce
from kairospy.reference.contracts import CryptoSpotSpec, ProductType
from kairospy.execution.order_state import DurableOrderStatus
from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore
from kairospy.governance.kill_switch import KillSwitch
from kairospy.runtime.clock import FixedClock
from kairospy.reference import ReferenceCatalog
from tests.reference_support import publish_test_instrument


def request(client_order_id: str = "client-1") -> OrderRequest:
    return OrderRequest(
        "internal-1",
        client_order_id,
        "strategy-v1",
        "intent-1",
        "correlation-1",
        AccountRef(InstitutionId("simulated"), "account-1", AccountType.SECURITIES_MARGIN),
        InstrumentId("instrument-1"),
        TradeSide.BUY,
        Decimal("1"),
        ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("10")),
    )


class RuntimeStoreTests(unittest.TestCase):
    def test_order_state_is_durable_idempotent_and_transition_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.sqlite3"
            store = SQLiteRuntimeStore(path)
            now = datetime(2026, 7, 17, tzinfo=timezone.utc)
            item = store.create_order(request(), now)
            self.assertEqual(item.status, DurableOrderStatus.PLANNED)
            self.assertEqual(store.create_order(request(), now), item)
            with self.assertRaises(ValueError):
                store.create_order(replace(request("client-1"), quantity=Decimal("2")), now)

            store.transition_order("client-1", DurableOrderStatus.APPROVED, now + timedelta(seconds=1))
            store.transition_order("client-1", DurableOrderStatus.SUBMITTING, now + timedelta(seconds=2))
            self.assertEqual(len(store.unresolved_orders()), 1)

            reopened = SQLiteRuntimeStore(path)
            recovered = reopened.order("client-1")
            assert recovered is not None
            self.assertEqual(recovered.status, DurableOrderStatus.SUBMITTING)

            ack = OrderAck(
                "internal-1", "client-1", "strategy-v1", "intent-1", "correlation-1",
                "venue-1", now + timedelta(seconds=3),
            )
            recovered = reopened.transition_order(
                "client-1", DurableOrderStatus.ACKNOWLEDGED, now + timedelta(seconds=3), ack=ack,
            )
            self.assertEqual(recovered.ack, ack)
            self.assertEqual(reopened.unresolved_orders(), ())
            with self.assertRaises(ValueError):
                reopened.transition_order("client-1", DurableOrderStatus.APPROVED, now + timedelta(seconds=4))

    def test_account_lock_prevents_two_runtime_owners(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            now = datetime(2026, 7, 17, tzinfo=timezone.utc)
            account = request().account
            store.acquire_account_lock(account, "runtime-a", now)
            store.acquire_account_lock(account, "runtime-a", now)
            with self.assertRaises(RuntimeError):
                store.acquire_account_lock(account, "runtime-b", now)
            store.release_account_lock(account, "runtime-a")
            store.acquire_account_lock(account, "runtime-b", now)

    def test_expired_account_lock_can_be_taken_over_but_active_lease_can_be_renewed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            now = datetime(2026, 7, 17, tzinfo=timezone.utc)
            account = request().account
            store.acquire_account_lock(account, "runtime-a", now, lease_seconds=10)
            store.heartbeat_account_lock(account, "runtime-a", now + timedelta(seconds=5), lease_seconds=10)
            with self.assertRaises(RuntimeError):
                store.acquire_account_lock(account, "runtime-b", now + timedelta(seconds=14))
            store.acquire_account_lock(account, "runtime-b", now + timedelta(seconds=16))

    def test_runtime_state_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            now = datetime(2026, 7, 17, tzinfo=timezone.utc)
            store.set_runtime_state("kill_switch", {"triggered": True, "reason": "drill"}, now)
            self.assertEqual(store.runtime_state("kill_switch"), {"reason": "drill", "triggered": True})

    def test_unresolved_order_manual_resolution_requires_audited_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            order = request()
            store.create_order(order, now := datetime(2026, 7, 17, tzinfo=timezone.utc))
            store.transition_order(order.client_order_id, DurableOrderStatus.APPROVED, now)
            store.transition_order(order.client_order_id, DurableOrderStatus.SUBMITTING, now)
            with self.assertRaisesRegex(ValueError, "actor, reason, and evidence"):
                store.resolve_unresolved_order(
                    order.client_order_id, DurableOrderStatus.REJECTED, now,
                    actor="", reason="", evidence="",
                )
            resolution = store.resolve_unresolved_order(
                order.client_order_id, DurableOrderStatus.REJECTED, now + timedelta(seconds=1),
                actor="operator@example.com", reason="confirmed crash before transport call",
                evidence="trace=runtime-failure-policy; venue-query=no-order",
            )
            self.assertEqual(resolution.previous_status, DurableOrderStatus.SUBMITTING)
            self.assertEqual(store.order(order.client_order_id).status, DurableOrderStatus.REJECTED)  # type: ignore[union-attr]
            self.assertEqual(store.unresolved_orders(), ())
            self.assertEqual(store.manual_order_resolutions(order.client_order_id), (resolution,))
            with self.assertRaisesRegex(ValueError, "not unresolved"):
                store.resolve_unresolved_order(
                    order.client_order_id, DurableOrderStatus.REJECTED, now + timedelta(seconds=2),
                    actor="operator@example.com", reason="repeat", evidence="repeat",
                )

    def test_open_orders_and_strategy_positions_rebuild_from_durable_facts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            order = request(); now = datetime(2026, 7, 17, tzinfo=timezone.utc)
            store.create_order(order, now)
            store.transition_order(order.client_order_id, DurableOrderStatus.APPROVED, now)
            store.transition_order(order.client_order_id, DurableOrderStatus.SUBMITTING, now)
            ack = OrderAck(
                order.internal_order_id, order.client_order_id, order.strategy_id, order.intent_id,
                order.correlation_id, "venue-open-1", now,
            )
            store.transition_order(order.client_order_id, DurableOrderStatus.ACKNOWLEDGED, now, ack=ack)
            self.assertEqual(store.local_open_order_ids(order.account), ("venue-open-1",))
            fill = TradeExecution(
                UUID("00000000-0000-0000-0000-000000000991"), now + timedelta(seconds=1),
                order.account, order.instrument_id, TradeSide.BUY, Decimal("1"), Decimal("10"),
                AssetId("USDT"), Decimal("0"), order.client_order_id,
            )
            instruments = ReferenceCatalog()
            publish_test_instrument(
                instruments, order.instrument_id, ProductType.CRYPTO_SPOT, "Test",
                CryptoSpotSpec(AssetId("BTC"), AssetId("USDT")), AssetId("USDT"),
                VenueId("simulated"), "BTCUSDT", datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
            transaction = LedgerService(Ledger(), instruments).build_trade(fill)
            store.commit_execution(
                "strategy-fill-991", fill, transaction, order.client_order_id,
                DurableOrderStatus.FILLED, fill.timestamp,
            )
            self.assertEqual(store.local_open_order_ids(order.account), ())
            positions = store.load_strategy_position_book(order.account).strategy_positions(order.strategy_id)
            self.assertEqual(len(positions), 1)
            self.assertEqual((positions[0].instrument_id, positions[0].quantity), (order.instrument_id, Decimal("1")))

    def test_ledger_import_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            ledger = Ledger()
            item = request()
            LedgerService(ledger, ReferenceCatalog()).deposit(
                item.account,
                AssetId("USDT"),
                Decimal("100"),
                datetime(2026, 7, 17, tzinfo=timezone.utc),
                "migration-deposit-1",
            )
            self.assertEqual(store.import_ledger(ledger), 1)
            self.assertEqual(store.import_ledger(ledger), 0)
            self.assertEqual(store.load_ledger().transactions, ledger.transactions)

    def test_kill_switch_state_survives_restart_and_requires_audited_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.sqlite3"
            now = datetime(2026, 7, 17, tzinfo=timezone.utc)
            store = SQLiteRuntimeStore(path)
            switch = KillSwitch((), FixedClock(now), store)
            switch.trigger((), "reconciliation mismatch")

            restarted = KillSwitch((), FixedClock(now), SQLiteRuntimeStore(path))
            self.assertTrue(restarted.triggered)
            self.assertTrue(restarted.reduce_only)
            with self.assertRaises(ValueError):
                restarted.reset(actor="", reason="")
            restarted.reset(actor="operator", reason="reconciled and approved")
            cleared = KillSwitch((), FixedClock(now), SQLiteRuntimeStore(path))
            self.assertFalse(cleared.triggered)
            self.assertFalse(cleared.reduce_only)


if __name__ == "__main__":
    unittest.main()
