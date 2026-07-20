from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from kairos.accounting.ledger import LedgerService
from kairos.ports import ComboLegRequest, ComboOrderRequest, Environment, OrderRequest
from kairos.connectors.simulated import SimulatedExecutionAccountGateway
from kairos.domain.capability import ExecutionCapabilities, OrderType
from kairos.domain.execution import TradeSide
from kairos.domain.identity import AccountKey, AccountType, AssetId, InstitutionId, InstrumentId, VenueId
from kairos.domain.ledger import Ledger
from kairos.domain.order import ExecutionInstructions, TimeInForce
from kairos.domain.intent import (
    CancelIntent, HedgeIntent, LegIntent, OpenStructureIntent, TransferIntent,
)
from kairos.domain.product import CryptoSpotSpec, ProductType
from kairos.execution.router import ExecutionRiskLimits, ExecutionRouter
from kairos.execution.planner import LeggingPolicy, NativeComboPlan, SequentialLegPlan, plan_combo
from kairos.execution.strategy_planner import plan_strategy_intent
from kairos.orchestration.coordinator import ExecutionCoordinator
from kairos.orchestration.event_log import PersistentEventLog
from kairos.orchestration.kill_switch import KillSwitch
from kairos.orchestration.monitoring import AlertSeverity, OperationalMonitor
from kairos.orchestration.reconciliation import ReconciliationService
from kairos.orchestration.runtime_store import SQLiteRuntimeStore
from tests.runtime_support import operational_application
from kairos.reference import BrokerId, ExecutionRoute, ListingId, ReferenceCatalog, RouteId
from tests.reference_support import publish_test_instrument


NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)
VENUE = VenueId("sim")
ACCOUNT = AccountKey(InstitutionId("sim"), "test", AccountType.CRYPTO_SPOT)
INSTRUMENT = InstrumentId("BTC-USDT")


def make_catalog() -> ReferenceCatalog:
    catalog = ReferenceCatalog(); effective_from = datetime(2020, 1, 1, tzinfo=timezone.utc)
    publish_test_instrument(catalog, INSTRUMENT, ProductType.CRYPTO_SPOT, "BTC-USDT", CryptoSpotSpec(AssetId("BTC"), AssetId("USDT"), Decimal("10")), AssetId("USDT"), VENUE, "BTCUSDT", effective_from, price_increment=Decimal("0.10"), quantity_increment=Decimal("0.01"), minimum_quantity=Decimal("0.01"), minimum_notional=Decimal("10"))
    catalog.routes.add(ExecutionRoute(RouteId("route:sim:test"), BrokerId("sim"), ACCOUNT, ListingId(f"listing:{VENUE.value}:{INSTRUMENT.value}"), effective_from))
    return catalog


def request(*, client_id: str = "client-1", quantity: str = "0.01", price: str = "1000", reduce_only: bool = False) -> OrderRequest:
    return OrderRequest(
        f"internal-{client_id}", client_id, "strategy-1", "intent-1", "correlation-1",
        ACCOUNT, INSTRUMENT, TradeSide.BUY, Decimal(quantity),
        ExecutionInstructions(OrderType.LIMIT, TimeInForce.GTC, Decimal(price), reduce_only=reduce_only),
    )


def coordinator(router, reconciliation, kill_switch, event_path) -> ExecutionCoordinator:
    path = Path(event_path)
    store = SQLiteRuntimeStore(path.parent / "runtime.sqlite3")
    return ExecutionCoordinator(
        router, reconciliation, kill_switch, PersistentEventLog(path),
        runtime_store=store,
        application=operational_application(path.parent, store),
    )


class OrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = make_catalog()
        self.ledger = Ledger()
        self.ledger_service = LedgerService(self.ledger, self.catalog)
        self.gateway = SimulatedExecutionAccountGateway(VENUE, ACCOUNT, environment=Environment.TESTNET)
        self.router = ExecutionRouter(self.catalog, (self.gateway,))

    def test_reconciliation_match_and_mismatch(self) -> None:
        service = ReconciliationService(self.ledger, self.gateway)
        self.assertTrue(service.reconcile(ACCOUNT).matched)
        self.ledger_service.deposit(ACCOUNT, AssetId("USDT"), Decimal("100"), NOW, "initial")
        report = service.reconcile(ACCOUNT)
        self.assertFalse(report.matched)
        self.assertEqual(report.differences[0].kind, "balance")
        self.assertEqual(report.differences[0].local, Decimal("100"))

    def test_readiness_refuses_start_and_orders_until_every_component_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            from kairos.application import ApplicationConfig, FunctionProbe, RuntimePaths, KairosApplication
            blocked_application = KairosApplication(
                ApplicationConfig(Environment.TESTNET, RuntimePaths.under(directory)), store,
                runtime_id="blocked-readiness",
                probes=(FunctionProbe("catalog", lambda: (False, "catalog unavailable")),),
            )
            blocked = ExecutionCoordinator(
                self.router, {ACCOUNT: ReconciliationService(self.ledger, self.gateway)},
                KillSwitch((self.gateway,)), PersistentEventLog(path),
                runtime_store=store, application=blocked_application,
            )
            with self.assertRaises(RuntimeError):
                blocked_application.start()
            with self.assertRaises(RuntimeError):
                blocked.submit(request(), NOW)
            ready = coordinator(
                self.router, {ACCOUNT: ReconciliationService(self.ledger, self.gateway)},
                KillSwitch((self.gateway,)), path,
            )
            self.assertEqual(ready.submit(request(), NOW).client_order_id, "client-1")

    def test_duplicate_client_id_is_idempotent_and_ack_links_intent(self) -> None:
        first = self.router.submit(request(), NOW)
        second = self.router.submit(request(), NOW)
        self.assertEqual(first, second)
        self.assertEqual(len(self.gateway.orders), 1)
        self.assertEqual(first.internal_order_id, "internal-client-1")
        self.assertEqual(first.strategy_id, "strategy-1")
        self.assertEqual(first.intent_id, "intent-1")
        self.assertEqual(first.correlation_id, "correlation-1")
        with self.assertRaisesRegex(ValueError, "different request"):
            self.router.submit(request(quantity="0.02"), NOW)

    def test_router_enforces_tick_lot_minimum_notional_and_capabilities(self) -> None:
        with self.assertRaisesRegex(ValueError, "lot"):
            self.router.submit(request(quantity="0.015"), NOW)
        with self.assertRaisesRegex(ValueError, "tick"):
            self.router.submit(request(price="1000.05"), NOW)
        with self.assertRaisesRegex(ValueError, "notional"):
            self.router.submit(request(price="500"), NOW)
        limited = SimulatedExecutionAccountGateway(VENUE, ACCOUNT)
        limited.capabilities = ExecutionCapabilities(frozenset({OrderType.MARKET}), frozenset(ProductType))
        with self.assertRaisesRegex(ValueError, "order type"):
            ExecutionRouter(self.catalog, (limited,)).submit(request(), NOW)
        strict = ExecutionRouter(self.catalog, (self.gateway,), ExecutionRiskLimits(Decimal("0.02"), Decimal("15")))
        with self.assertRaisesRegex(ValueError, "quantity exceeds"):
            strict.submit(request(quantity="0.03", price="1000"), NOW)
        with self.assertRaisesRegex(ValueError, "notional exceeds"):
            strict.submit(request(quantity="0.02", price="1000"), NOW)

    def test_disconnect_and_reconnect(self) -> None:
        self.gateway.disconnect()
        with self.assertRaises(ConnectionError):
            self.gateway.account_state(ACCOUNT)
        with self.assertRaises(ConnectionError):
            self.gateway.place_order(request())
        self.gateway.reconnect()
        self.assertEqual(self.gateway.account_state(ACCOUNT).account, ACCOUNT)

    def test_event_log_deduplicates_and_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/events.jsonl"
            log = PersistentEventLog(path)
            log.append("event-1", "test", {"value": 1})
            log.append("event-1", "test", {"value": 2})
            self.assertEqual(len(log.read()), 1)
            reloaded = PersistentEventLog(path)
            reloaded.append("event-1", "test", {"value": 3})
            reloaded.append("event-2", "test", {"value": 4})
            self.assertEqual([row["event_id"] for row in reloaded.read()], ["event-1", "event-2"])

    def test_coordinator_restart_reuses_persisted_order_ack_without_resubmission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/events.jsonl"
            first = coordinator(
                self.router, {ACCOUNT: ReconciliationService(self.ledger, self.gateway)},
                KillSwitch((self.gateway,)), path,
            )
            original = first.submit(request(), NOW)
            restarted_gateway = SimulatedExecutionAccountGateway(VENUE, ACCOUNT)
            restarted = coordinator(
                ExecutionRouter(self.catalog, (restarted_gateway,)),
                {ACCOUNT: ReconciliationService(self.ledger, restarted_gateway)},
                KillSwitch((restarted_gateway,)), path,
            )
            recovered = restarted.submit(request(), NOW)
            self.assertEqual(recovered, original)
            self.assertEqual(restarted_gateway.orders, {})

    def test_kill_switch_cancels_orders_and_allows_only_reduce_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kill_switch = KillSwitch((self.gateway,))
            runtime = coordinator(
                self.router, {ACCOUNT: ReconciliationService(self.ledger, self.gateway)},
                kill_switch, f"{directory}/events.jsonl",
            )
            runtime.submit(request(client_id="normal"), NOW)
            result = kill_switch.trigger((ACCOUNT,), "drill")
            self.assertEqual(len(result.cancelled_orders), 1)
            self.assertTrue(kill_switch.reduce_only)
            self.assertEqual(self.gateway.open_orders(ACCOUNT), ())
            with self.assertRaisesRegex(RuntimeError, "only reduce-only"):
                runtime.submit(request(client_id="blocked"), NOW)
            self.gateway.positions[INSTRUMENT] = Decimal("-0.02")
            ack = runtime.submit(request(client_id="reduce", reduce_only=True), NOW)
            self.assertEqual(ack.intent_id, "intent-1")

    def test_operational_monitor_surfaces_clock_rate_disconnect_and_authentication(self) -> None:
        monitor = OperationalMonitor(maximum_clock_skew_ms=500)
        monitor.clock_skew("binance", 750)
        monitor.rate_limit("binance", 90, 100)
        monitor.disconnected("ibkr", "gateway lost")
        monitor.authentication_error("binance", "invalid signature")
        self.assertEqual(len(monitor.alerts), 4)
        self.assertEqual(monitor.alerts[0].severity, AlertSeverity.CRITICAL)

    def test_combo_planner_prefers_native_and_never_silently_legs(self) -> None:
        combo = ComboOrderRequest(
            "combo-internal", "combo-client", "strategy", "intent", "correlation", ACCOUNT,
            (ComboLegRequest(INSTRUMENT, TradeSide.BUY, 1), ComboLegRequest(INSTRUMENT, TradeSide.SELL, 1)),
            Decimal("1"), ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("10")),
        )
        self.assertIsInstance(plan_combo(combo, self.gateway.capabilities), NativeComboPlan)
        limited = ExecutionCapabilities(self.gateway.capabilities.order_types, frozenset(ProductType))
        with self.assertRaisesRegex(ValueError, "silent legging"):
            plan_combo(combo, limited)
        plan = plan_combo(combo, limited, legging_policy=LeggingPolicy.SEQUENTIAL, maximum_naked_legs=1)
        self.assertIsInstance(plan, SequentialLegPlan)
        self.assertEqual(len(plan.requests), 2)

    def test_all_intent_categories_have_typed_execution_plans(self) -> None:
        intent_id = UUID("00000000-0000-0000-0000-000000000123")
        instructions = {INSTRUMENT: ExecutionInstructions(OrderType.LIMIT, TimeInForce.GTC, Decimal("1000"))}
        hedge = plan_strategy_intent(
            HedgeIntent(intent_id, "hedger", INSTRUMENT, Decimal("-0.25"), "delta hedge"),
            accounts={INSTRUMENT: ACCOUNT}, current_positions={}, instructions=instructions,
        )
        self.assertEqual((hedge.orders[0].side, hedge.orders[0].quantity), (TradeSide.SELL, Decimal("0.25")))

        structure = plan_strategy_intent(
            OpenStructureIntent(
                "spread", (LegIntent(INSTRUMENT, TradeSide.BUY), LegIntent(INSTRUMENT, TradeSide.SELL)),
                1, Decimal("10"), TimeInForce.DAY, "open", intent_id,
            ),
            accounts={INSTRUMENT: ACCOUNT}, current_positions={}, instructions=instructions,
        )
        self.assertEqual(len(structure.combo_orders), 1)
        self.assertEqual(len(structure.combo_orders[0].legs), 2)

        other_account = AccountKey(InstitutionId("sim"), "other", AccountType.CRYPTO_SPOT)
        transfer_intent = TransferIntent(intent_id, "allocator", ACCOUNT, other_account, AssetId("USDT"), Decimal("100"), "rebalance")
        transfer = plan_strategy_intent(transfer_intent, accounts={}, current_positions={}, instructions={})
        self.assertEqual(transfer.transfers, (transfer_intent,))
        cancel_intent = CancelIntent(intent_id, "operator", "client-1", "risk")
        cancellation = plan_strategy_intent(cancel_intent, accounts={}, current_positions={}, instructions={})
        self.assertEqual(cancellation.cancellations, (cancel_intent,))

    def test_native_combo_and_cancel_intent_pass_through_coordinator_and_event_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = coordinator(
                self.router, {ACCOUNT: ReconciliationService(self.ledger, self.gateway)},
                KillSwitch((self.gateway,)), f"{directory}/events.jsonl",
            )
            combo = ComboOrderRequest(
                "combo-internal", "combo-client", "spread", "combo-intent", "combo-correlation",
                ACCOUNT,
                (ComboLegRequest(INSTRUMENT, TradeSide.BUY, 1), ComboLegRequest(INSTRUMENT, TradeSide.SELL, 1)),
                Decimal("1"), ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("10")),
            )
            ack = runtime.submit_combo(combo, NOW)
            self.assertEqual(runtime.submit_combo(combo, NOW), ack)
            self.assertEqual(len(self.gateway.orders), 1)
            with self.assertRaisesRegex(ValueError, "different request"):
                runtime.submit(request(client_id="combo-client"), NOW)
            cancel = CancelIntent(UUID("00000000-0000-0000-0000-000000000124"), "spread", "combo-client", "risk exit")
            runtime.cancel(cancel, ACCOUNT)
            runtime.cancel(cancel, ACCOUNT)
            self.assertEqual(self.gateway.orders, {})
            event_types = [item["event_type"] for item in runtime.event_log.read()]
            self.assertIn("combo_order_ack", event_types)
            self.assertIn("order_cancelled", event_types)


if __name__ == "__main__":
    unittest.main()
