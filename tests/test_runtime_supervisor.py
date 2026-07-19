from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO
import json

from trading.adapters.base import Environment
from trading.adapters.simulated import SimulatedExecutionAccountAdapter
from trading.application import (
    ApplicationConfig, FixedClock, RuntimePaths, RuntimeRecoveryService,
    RuntimeStatus, RuntimeSupervisor, TradingApplication,
    write_soak_artifact,
)
from trading.domain.identity import AccountKey, AccountType, AssetId, InstitutionId, VenueId
from trading.domain.identity import InstrumentId
from trading.domain.product import CryptoSpotSpec, ProductType
from trading.reference import BrokerId, ExecutionRoute, ListingId, ReferenceCatalog, ReferenceCatalogRepository, RouteId
from tests.reference_support import publish_test_instrument
from trading.strategies.specs import register_builtin_strategies
from trading.__main__ import main
from trading.domain.strategy_contract import StrategyLifecycle
from trading.strategies.promotion import evaluate_promotion_artifacts
from trading.domain.ledger import Ledger
from trading.execution.recovery import OrderRecoveryReport
from trading.orchestration.kill_switch import KillSwitch
from trading.orchestration.monitoring import OperationalMonitor
from trading.orchestration.reconciliation import ReconciliationService
from trading.orchestration.runtime_store import SQLiteRuntimeStore
from tests.test_durable_execution_ingestion import catalog
from tests.test_runtime_store import request


NOW = datetime(2026, 7, 17, 14, 0, tzinfo=timezone.utc)


class BackgroundService:
    def __init__(self) -> None:
        self.starts = self.backfills = self.stops = 0
        self.complete = True
    def start(self): self.starts += 1; return OrderRecoveryReport((), ())
    def backfill(self):
        self.backfills += 1
        return OrderRecoveryReport((), () if self.complete else ("unknown",))
    def stop(self): self.stops += 1


class RuntimeSupervisorTests(unittest.TestCase):
    def _build(self, directory: str):
        paths = RuntimePaths.under(directory)
        store = SQLiteRuntimeStore(paths.runtime_database)
        account = request().account
        clock = FixedClock(NOW)
        adapter = SimulatedExecutionAccountAdapter(VenueId("simulated"), account, clock=clock)
        app = TradingApplication(
            ApplicationConfig(Environment.TESTNET, paths), store, runtime_id="supervised-runtime",
            accounts=(account,), clock=clock,
            recovery=RuntimeRecoveryService(store, catalog(), AssetId("USDT"), {account: adapter}),
        )
        reconciliation = ReconciliationService(
            Ledger(), adapter, clock=clock, runtime_store=store,
            strategy_positions=store.load_strategy_position_book(account),
        )
        switch = KillSwitch((adapter,), clock, store)
        background = BackgroundService()
        supervisor = RuntimeSupervisor(
            app, {account: reconciliation}, switch, OperationalMonitor(clock=clock),
            background_services=(background,), clock=clock,
        )
        return supervisor, app, store, adapter, switch, background

    def test_healthy_cycles_heartbeat_recover_reconcile_and_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            supervisor, app, store, _, switch, background = self._build(directory)
            supervisor.start()
            cycles = supervisor.run_cycles(3)
            self.assertTrue(all(item.healthy for item in cycles))
            self.assertEqual((background.starts, background.backfills), (1, 3))
            self.assertFalse(switch.triggered)
            checkpoint = store.runtime_state(RuntimeSupervisor.STATE_KEY)
            self.assertEqual(checkpoint["cycle_count"], 3)  # type: ignore[index]
            self.assertEqual(checkpoint["status"], "running")  # type: ignore[index]
            supervisor.stop()
            self.assertEqual(background.stops, 1)
            self.assertEqual(app.status, RuntimeStatus.STOPPED)

    def test_periodic_mismatch_triggers_persistent_kill_switch_and_reduce_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            supervisor, app, store, adapter, switch, _ = self._build(directory)
            supervisor.start()
            adapter.balances[AssetId("USDT")] = Decimal("1")
            cycle = supervisor.run_cycle()
            self.assertFalse(cycle.healthy)
            self.assertTrue(switch.triggered)
            self.assertEqual(app.status, RuntimeStatus.REDUCE_ONLY)
            persisted = SQLiteRuntimeStore(store.path).runtime_state(KillSwitch.STATE_KEY)
            self.assertTrue(persisted["triggered"])  # type: ignore[index]
            supervisor.stop()

    def test_incomplete_background_recovery_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            supervisor, app, _, _, switch, background = self._build(directory)
            supervisor.start()
            background.complete = False
            cycle = supervisor.run_cycle()
            self.assertFalse(cycle.recovery_complete)
            self.assertTrue(switch.triggered)
            self.assertEqual(app.status, RuntimeStatus.REDUCE_ONLY)
            supervisor.stop()

    def test_soak_artifact_requires_duration_healthy_cycles_and_safety_drills(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            supervisor, _, _, _, _, _ = self._build(directory)
            supervisor.start(); supervisor.run_cycles(2); supervisor.stop()
            payload = write_soak_artifact(
                supervisor, Path(directory) / "soak.json",
                started_at=NOW, ended_at=NOW + timedelta(hours=24),
                target_duration_seconds=24 * 3600, environment="testnet",
                restart_drill_passed=True, kill_switch_drill_passed=True,
            )
            self.assertTrue(payload["passed"])
            self.assertEqual(payload["kind"], "runtime_l4_soak")
            self.assertEqual(len(payload["audit_hash"]), 64)
            artifact_payload = json.loads((Path(directory) / "soak.json").read_text(encoding="utf-8"))
            self.assertEqual(artifact_payload["kind"], "runtime_l4_soak")
            self.assertTrue(evaluate_promotion_artifacts(StrategyLifecycle.LIVE_LIMITED, (artifact_payload,)).passed)
            failed = write_soak_artifact(
                supervisor, Path(directory) / "short-soak.json",
                started_at=NOW, ended_at=NOW + timedelta(hours=1),
                target_duration_seconds=24 * 3600, environment="testnet",
                restart_drill_passed=True, kill_switch_drill_passed=True,
            )
            self.assertFalse(failed["passed"])

    def test_simulated_trade_cli_runs_supervised_soak_restart_and_kill_drills(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            register_builtin_strategies(root / "strategies")
            instruments = ReferenceCatalog(); instrument_id = InstrumentId("BTC-USDT-SOAK")
            effective_from = datetime(2020, 1, 1, tzinfo=timezone.utc)
            publish_test_instrument(instruments, instrument_id, ProductType.CRYPTO_SPOT, "BTC/USDT", CryptoSpotSpec(AssetId("BTC"), AssetId("USDT")), AssetId("USDT"), VenueId("simulated"), "BTCUSDT", effective_from)
            account = AccountKey(InstitutionId("simulated"), "default", AccountType.CRYPTO_SPOT)
            instruments.routes.add(ExecutionRoute(RouteId("route:soak"), BrokerId("simulated"), account, ListingId(f"listing:simulated:{instrument_id.value}"), effective_from))
            catalog_path = root / "catalog" / "instruments.json"
            ReferenceCatalogRepository(catalog_path).save(instruments)
            artifact = root / "soak.json"
            output = StringIO()
            with redirect_stdout(output):
                code = main([
                    "--lake-root", str(root), "--reference-catalog-path", str(catalog_path),
                    "--event-log-path", str(root / "events.jsonl"),
                    "--runtime-db", str(root / "runtime.sqlite3"),
                    "trade", "run", "--strategy", "spot-perp-carry", "--venue", "simulated",
                    "--environment", "testnet", "--instrument", "BTC-USDT-SOAK",
                    "--side", "buy", "--quantity", "1", "--limit-price", "100",
                    "--soak-seconds", "1", "--cycle-seconds", "0.1",
                    "--kill-switch-drill", "--restart-drill", "--soak-artifact", str(artifact),
                ])
            self.assertEqual(code, 0, output.getvalue())
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertTrue(payload["passed"])
            self.assertGreater(payload["cycle_count"], 0)

    def test_l4_preflight_reports_missing_external_and_governance_prerequisites(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"BINANCE_TESTNET_API_KEY": "", "BINANCE_TESTNET_API_SECRET": ""}, clear=False,
        ):
            artifact = Path(directory) / "preflight.json"
            output = StringIO()
            with redirect_stdout(output):
                code = main([
                    "--lake-root", directory, "--catalog-path", str(Path(directory) / "missing.json"),
                    "runtime", "l4-preflight", "--venue", "binance", "--environment", "testnet",
                    "--strategy", "spot-perp-carry", "--instrument", "missing",
                    "--evidence-artifact", str(artifact),
                ])
            payload = json.loads(output.getvalue())
            evidence = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(code, 2)
            self.assertEqual(payload["kind"], "runtime_l4_preflight")
            self.assertFalse(payload["ready"])
            self.assertEqual(payload["artifact"], str(artifact))
            self.assertEqual(evidence["kind"], "runtime_l4_preflight")
            self.assertEqual(evidence["ready"], payload["ready"])
            self.assertEqual(len(evidence["audit_hash"]), 64)
            self.assertFalse(payload["checks"]["external_connection_ready"])
            self.assertFalse(payload["checks"]["strategy_paper_approved"])


if __name__ == "__main__":
    unittest.main()
