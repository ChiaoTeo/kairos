from __future__ import annotations

import json
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from kairospy.__main__ import main
from kairospy.application.runtime_reference_artifact import RUNTIME_REFERENCE_SCENARIO_ID, run_runtime_reference_artifact
from kairospy.execution.order_state import DurableOrderStatus
from kairospy.orchestration.runtime_store import SQLiteRuntimeStore
from tests.test_runtime_store import request


EXPECTED_AUDIT_HASH = "5d026b6d2d139b4f9ed3e3e7c047ff61010fe6d9d5764e6cc081307e58def031"
EXPECTED_LEDGER_HASH = "a734aa836750bc2a669ce59ce231a1d93c1e91a014a0e393133b188ab1b005a8"


class RuntimeReferenceArtifactTests(unittest.TestCase):
    def test_formal_runtime_chain_survives_restart_with_fixed_audit_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_runtime_reference_artifact(directory)
            self.assertEqual(result.scenario_id, RUNTIME_REFERENCE_SCENARIO_ID)
            self.assertEqual(result.audit_hash, EXPECTED_AUDIT_HASH)
            self.assertEqual(result.payload["ledger_hash"], EXPECTED_LEDGER_HASH)
            self.assertEqual(result.payload["stages"], [
                "market_data", "strategy", "intent", "risk", "order", "fill", "ledger",
                "portfolio", "reconciliation", "ready_after_restart",
            ])
            self.assertEqual(result.payload["durable_order_status"], "filled")
            self.assertEqual(result.payload["restart_status"], "ready")
            manifest = json.loads(result.artifact.read_text(encoding="utf-8"))
            self.assertEqual(manifest["audit_hash"], EXPECTED_AUDIT_HASH)
            self.assertEqual(manifest["portfolio"]["status"], "complete")
            self.assertTrue(manifest["reconciliations"][0]["matched"])
            rerun = run_runtime_reference_artifact(directory)
            self.assertEqual(rerun.audit_hash, EXPECTED_AUDIT_HASH)

    def test_runtime_reference_artifact_cli_writes_product_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(main(["runtime", "reference-artifact", "--root", directory]), 0)
            artifact = Path(directory) / "artifacts" / RUNTIME_REFERENCE_SCENARIO_ID / "manifest.json"
            self.assertTrue(artifact.is_file())

    def test_runtime_orders_cli_lists_and_audits_manual_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.sqlite3"
            store = SQLiteRuntimeStore(path)
            order = request(); now = datetime(2026, 7, 17, tzinfo=timezone.utc)
            store.create_order(order, now)
            store.transition_order(order.client_order_id, DurableOrderStatus.APPROVED, now)
            store.transition_order(order.client_order_id, DurableOrderStatus.SUBMITTING, now)
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["runtime", "orders", "--db", str(path)]), 0)
            self.assertIn(order.client_order_id, output.getvalue())
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([
                    "runtime", "orders", "--db", str(path),
                    "--client-order-id", order.client_order_id, "--target", "rejected",
                    "--actor", "operator", "--reason", "confirmed absent",
                    "--evidence", "venue-query=no-order",
                ]), 0)
            self.assertIn("venue-query=no-order", output.getvalue())
            self.assertEqual(store.unresolved_orders(), ())


if __name__ == "__main__":
    unittest.main()
