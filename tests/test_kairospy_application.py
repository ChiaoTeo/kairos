from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from kairospy.accounting.ledger import LedgerService
from kairospy.ports import Environment, OrderAck
from kairospy.connectors.simulated import SimulatedExecutionAccountGateway
from kairospy.application import (
    ApplicationConfig, FixedClock, FunctionProbe, RuntimePaths, RuntimeRecoveryService, RuntimeStatus,
    KairosApplication,
)
from kairospy.trading.execution import TradeExecution, TradeSide
from kairospy.trading.identity import AssetId, VenueId
from kairospy.trading.ledger import Ledger
from kairospy.trading.product import ContractType, FutureSpec, ProductType
from kairospy.execution.ingestion import DurableExecutionIngestionService
from kairospy.execution.order_state import DurableOrderStatus
from kairospy.orchestration.runtime_store import SQLiteRuntimeStore
from tests.test_durable_execution_ingestion import catalog
from tests.test_runtime_store import request
from kairospy.reference import ReferenceCatalog
from tests.reference_support import publish_test_instrument


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class KairosApplicationTests(unittest.TestCase):
    def test_runtime_lifecycle_uses_real_probes_and_releases_account_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths.under(directory)
            store = SQLiteRuntimeStore(paths.runtime_database)
            account = request().account
            recovery = RuntimeRecoveryService(
                store,
                catalog(),
                AssetId("USDT"),
                {account: SimulatedExecutionAccountGateway(VenueId("simulated"), account)},
            )
            application = KairosApplication(
                ApplicationConfig(Environment.TESTNET, paths), store, runtime_id="runtime-a",
                accounts=(account,), probes=(FunctionProbe("catalog", lambda: (True, "loaded")),),
                recovery=recovery,
                clock=FixedClock(NOW),
            )
            application.start()
            self.assertEqual(application.status, RuntimeStatus.READY)
            self.assertEqual([item.name for item in application.probe_results], ["persistence", "catalog"])
            application.run()
            self.assertEqual(application.status, RuntimeStatus.RUNNING)
            application.heartbeat()
            application.degrade("market data stale")
            self.assertEqual(application.status, RuntimeStatus.REDUCE_ONLY)
            application.stop()
            self.assertEqual(application.status, RuntimeStatus.STOPPED)

            second = KairosApplication(
                ApplicationConfig(Environment.TESTNET, paths), store, runtime_id="runtime-b",
                accounts=(account,),
                recovery=RuntimeRecoveryService(
                    store,
                    catalog(),
                    AssetId("USDT"),
                    {account: SimulatedExecutionAccountGateway(VenueId("simulated"), account)},
                ),
                clock=FixedClock(NOW),
            )
            second.start()
            second.stop()

    def test_failed_probe_prevents_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths.under(directory)
            application = KairosApplication(
                ApplicationConfig(Environment.TESTNET, paths), SQLiteRuntimeStore(paths.runtime_database),
                runtime_id="runtime-a", probes=(FunctionProbe("market_data", lambda: (False, "stale")),),
                clock=FixedClock(NOW),
            )
            with self.assertRaisesRegex(RuntimeError, "market_data"):
                application.start()
            self.assertEqual(application.status, RuntimeStatus.FAILED_START)
            application.stop()

    def test_account_runtime_requires_recovery_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths.under(directory)
            with self.assertRaisesRegex(ValueError, "require durable recovery"):
                KairosApplication(
                    ApplicationConfig(Environment.TESTNET, paths),
                    SQLiteRuntimeStore(paths.runtime_database),
                    runtime_id="unsafe-runtime",
                    accounts=(request().account,),
                    clock=FixedClock(NOW),
                )

    def test_unresolved_order_stops_startup_before_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths.under(directory)
            store = SQLiteRuntimeStore(paths.runtime_database)
            item = request()
            store.create_order(item, NOW)
            store.transition_order(item.client_order_id, DurableOrderStatus.APPROVED, NOW)
            store.transition_order(item.client_order_id, DurableOrderStatus.SUBMITTING, NOW)
            probe_calls = []
            application = KairosApplication(
                ApplicationConfig(Environment.TESTNET, paths), store, runtime_id="runtime-a",
                probes=(FunctionProbe("should_not_run", lambda: (probe_calls.append(True) or True, "ok")),),
                clock=FixedClock(NOW),
            )
            with self.assertRaisesRegex(RuntimeError, "venue resolution"):
                application.start()
            self.assertEqual(application.status, RuntimeStatus.UNKNOWN_EXTERNAL_STATE)
            self.assertEqual(probe_calls, [])
            application.stop()

    def test_restart_rebuilds_ledger_portfolio_risk_and_reconciles_before_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths.under(directory)
            store = SQLiteRuntimeStore(paths.runtime_database)
            order = request()
            store.create_order(order, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.APPROVED, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.SUBMITTING, NOW)
            store.transition_order(
                order.client_order_id,
                DurableOrderStatus.ACKNOWLEDGED,
                NOW,
                ack=OrderAck(
                    order.internal_order_id,
                    order.client_order_id,
                    order.strategy_id,
                    order.intent_id,
                    order.correlation_id,
                    "venue-order-1",
                    NOW,
                ),
            )
            ingestion = DurableExecutionIngestionService(LedgerService(Ledger(), catalog()), store)
            ingestion.ingest(
                TradeExecution(
                    UUID("00000000-0000-0000-0000-000000000201"),
                    NOW + timedelta(seconds=1),
                    order.account,
                    order.instrument_id,
                    TradeSide.BUY,
                    Decimal("1"),
                    Decimal("10"),
                    AssetId("USDT"),
                    Decimal("0.1"),
                    order.client_order_id,
                ),
                external_key="simulated:recovery-fill-1",
                client_order_id=order.client_order_id,
                fully_filled=True,
                cursor_name="simulated:fills",
                cursor_value="201",
            )
            restarted_store = SQLiteRuntimeStore(paths.runtime_database)
            gateway = SimulatedExecutionAccountGateway(
                VenueId("simulated"),
                order.account,
                balances=((AssetId("USDT"), Decimal("-10.1")),),
                positions=((order.instrument_id, Decimal("1")),),
            )
            recovery = RuntimeRecoveryService(
                restarted_store,
                catalog(),
                AssetId("USDT"),
                {order.account: gateway},
                marks={order.instrument_id: Decimal("10")},
            )
            application = KairosApplication(
                ApplicationConfig(Environment.TESTNET, paths),
                restarted_store,
                runtime_id="runtime-after-restart",
                accounts=(order.account,),
                recovery=recovery,
                clock=FixedClock(NOW + timedelta(seconds=2)),
            )
            application.start()

            result = application.recovery_result
            assert result is not None
            self.assertEqual(application.status, RuntimeStatus.READY)
            self.assertEqual(len(result.ledger.transactions), 1)
            self.assertEqual(result.portfolio.status, "complete")
            self.assertEqual(result.portfolio.positions[0].quantity, Decimal("1"))
            self.assertEqual(result.risk.gross_exposure, Decimal("10"))
            self.assertTrue(result.reconciliations[0].matched)
            persisted = restarted_store.runtime_state(RuntimeRecoveryService.STATE_KEY)
            assert isinstance(persisted, dict)
            self.assertTrue(persisted["ready"])
            self.assertEqual(persisted["ledger_transaction_count"], 1)
            application.stop()

    def test_reconciliation_mismatch_fails_closed_before_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths.under(directory)
            store = SQLiteRuntimeStore(paths.runtime_database)
            order = request()
            gateway = SimulatedExecutionAccountGateway(
                VenueId("simulated"),
                order.account,
                balances=((AssetId("USDT"), Decimal("100")),),
            )
            recovery = RuntimeRecoveryService(
                store,
                catalog(),
                AssetId("USDT"),
                {order.account: gateway},
            )
            application = KairosApplication(
                ApplicationConfig(Environment.TESTNET, paths),
                store,
                runtime_id="runtime-mismatch",
                accounts=(order.account,),
                recovery=recovery,
                clock=FixedClock(NOW),
            )
            with self.assertRaisesRegex(RuntimeError, "reconciliation mismatches"):
                application.start()
            self.assertEqual(application.status, RuntimeStatus.UNKNOWN_EXTERNAL_STATE)
            self.assertIsNotNone(application.recovery_result)
            application.stop()

    def test_open_order_mismatch_fails_closed_before_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths.under(directory)
            store = SQLiteRuntimeStore(paths.runtime_database)
            order = request()
            store.create_order(order, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.APPROVED, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.SUBMITTING, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.ACKNOWLEDGED, NOW, ack=OrderAck(
                order.internal_order_id, order.client_order_id, order.strategy_id, order.intent_id,
                order.correlation_id, "local-only-open-order", NOW,
            ))
            gateway = SimulatedExecutionAccountGateway(
                VenueId("simulated"), order.account, clock=FixedClock(NOW),
            )
            application = KairosApplication(
                ApplicationConfig(Environment.TESTNET, paths), store,
                runtime_id="open-order-mismatch", accounts=(order.account,), clock=FixedClock(NOW),
                recovery=RuntimeRecoveryService(
                    store, catalog(), AssetId("USDT"), {order.account: gateway},
                ),
            )
            with self.assertRaisesRegex(RuntimeError, "reconciliation mismatches"):
                application.start()
            result = application.recovery_result
            assert result is not None
            self.assertEqual(result.reconciliations[0].differences[0].kind, "open_order")
            application.stop()

    def test_strategy_position_projection_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths.under(Path(directory) / "target")
            source = SQLiteRuntimeStore(Path(directory) / "source.sqlite3")
            order = request()
            source.create_order(order, NOW)
            source.transition_order(order.client_order_id, DurableOrderStatus.APPROVED, NOW)
            source.transition_order(order.client_order_id, DurableOrderStatus.SUBMITTING, NOW)
            source.transition_order(order.client_order_id, DurableOrderStatus.ACKNOWLEDGED, NOW, ack=OrderAck(
                order.internal_order_id, order.client_order_id, order.strategy_id, order.intent_id,
                order.correlation_id, "venue-order-strategy", NOW,
            ))
            DurableExecutionIngestionService(LedgerService(Ledger(), catalog()), source).ingest(
                TradeExecution(
                    UUID("00000000-0000-0000-0000-000000000777"), NOW + timedelta(seconds=1),
                    order.account, order.instrument_id, TradeSide.BUY, Decimal("1"), Decimal("10"),
                    AssetId("USDT"), Decimal("0.1"), order.client_order_id,
                ),
                external_key="strategy-projection-source", client_order_id=order.client_order_id,
                fully_filled=True,
            )
            target = SQLiteRuntimeStore(paths.runtime_database)
            target.import_ledger(source.load_ledger())
            gateway = SimulatedExecutionAccountGateway(
                VenueId("simulated"), order.account,
                balances=((AssetId("USDT"), Decimal("-10.1")),),
                positions=((order.instrument_id, Decimal("1")),), clock=FixedClock(NOW + timedelta(seconds=2)),
            )
            application = KairosApplication(
                ApplicationConfig(Environment.TESTNET, paths), target,
                runtime_id="strategy-position-mismatch", accounts=(order.account,),
                clock=FixedClock(NOW + timedelta(seconds=2)),
                recovery=RuntimeRecoveryService(
                    target, catalog(), AssetId("USDT"), {order.account: gateway},
                    marks={order.instrument_id: Decimal("10")},
                ),
            )
            with self.assertRaisesRegex(RuntimeError, "reconciliation mismatches"):
                application.start()
            result = application.recovery_result
            assert result is not None
            self.assertEqual(result.reconciliations[0].differences[0].kind, "strategy_position")
            application.stop()

    def test_expired_derivative_position_requires_durable_settlement_before_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths.under(directory)
            store = SQLiteRuntimeStore(paths.runtime_database)
            base_order = request()
            expiry = NOW + timedelta(hours=1)
            instrument = replace(base_order.instrument_id, value="future-expiring-1")
            order = replace(base_order, instrument_id=instrument)
            instruments = ReferenceCatalog()
            publish_test_instrument(
                instruments, instrument, ProductType.FUTURE, "BTC Future",
                FutureSpec(AssetId("BTC"), AssetId("USDT"), expiry, Decimal("1"), ContractType.LINEAR, "BTC-index"),
                AssetId("USDT"), VenueId("simulated"), "BTC-FUT", datetime(2020, 1, 1, tzinfo=timezone.utc),
                price_increment=Decimal("0.1"),
            )
            store.create_order(order, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.APPROVED, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.SUBMITTING, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.ACKNOWLEDGED, NOW, ack=OrderAck(
                order.internal_order_id, order.client_order_id, order.strategy_id, order.intent_id,
                order.correlation_id, "future-order", NOW,
            ))
            DurableExecutionIngestionService(LedgerService(Ledger(), instruments), store).ingest(
                TradeExecution(
                    UUID("00000000-0000-0000-0000-000000000778"), NOW + timedelta(seconds=1),
                    order.account, instrument, TradeSide.BUY, Decimal("1"), Decimal("100"),
                    AssetId("USDT"), Decimal("0"), order.client_order_id,
                ), external_key="future-open-fill", client_order_id=order.client_order_id, fully_filled=True,
            )
            after_expiry = expiry + timedelta(seconds=1)
            gateway = SimulatedExecutionAccountGateway(
                VenueId("simulated"), order.account, positions=((instrument, Decimal("1")),),
                clock=FixedClock(after_expiry),
            )
            application = KairosApplication(
                ApplicationConfig(Environment.TESTNET, paths), store,
                runtime_id="expired-settlement-gate", accounts=(order.account,), clock=FixedClock(after_expiry),
                recovery=RuntimeRecoveryService(
                    store, instruments, AssetId("USDT"), {order.account: gateway},
                    marks={instrument: Decimal("100")},
                ),
            )
            with self.assertRaisesRegex(RuntimeError, "expired positions require durable settlement"):
                application.start()
            application.stop()


if __name__ == "__main__":
    unittest.main()
