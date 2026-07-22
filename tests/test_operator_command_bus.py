from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from kairospy.runtime import OperatorCommandBus, OperatorCommandStatus, OperatorCommandType
from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore


NOW = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)


class OperatorCommandBusTests(unittest.TestCase):
    def test_operator_command_bus_submits_claims_and_completes_idempotent_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            bus = OperatorCommandBus(store)

            command = bus.submit(
                run_id="live-a",
                command_type=OperatorCommandType.STOP,
                payload={"graceful": True},
                actor="cli",
                reason="maintenance",
                idempotency_key="stop:maintenance",
                at=NOW,
            )
            duplicate = bus.submit(
                run_id="live-a",
                command_type="stop",
                payload={"graceful": True},
                actor="cli",
                reason="maintenance",
                idempotency_key="stop:maintenance",
                at=NOW,
            )

            self.assertEqual(duplicate.command_id, command.command_id)
            self.assertEqual(command.status, OperatorCommandStatus.PENDING)

            claimed = bus.claim_next(
                run_id="live-a",
                claimed_by="daemon-a",
                at=NOW,
                command_types=(OperatorCommandType.STOP,),
            )
            assert claimed is not None
            self.assertEqual(claimed.status, OperatorCommandStatus.CLAIMED)
            self.assertEqual(claimed.claimed_by, "daemon-a")

            running = bus.start(claimed.command_id, NOW)
            self.assertEqual(running.status, OperatorCommandStatus.RUNNING)
            completed = bus.complete(running.command_id, {"phase": "stopped"}, NOW)

            self.assertEqual(completed.status, OperatorCommandStatus.SUCCEEDED)
            self.assertEqual(completed.result, {"phase": "stopped"})
            self.assertIsNone(bus.claim_next(run_id="live-a", claimed_by="daemon-a", at=NOW))

    def test_operator_command_idempotency_key_rejects_conflicting_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            bus = OperatorCommandBus(store)
            bus.submit(
                run_id="live-a",
                command_type=OperatorCommandType.STOP,
                payload={},
                actor="cli",
                reason="maintenance",
                idempotency_key="same",
                at=NOW,
            )

            with self.assertRaisesRegex(ValueError, "idempotency key"):
                bus.submit(
                    run_id="live-a",
                    command_type=OperatorCommandType.PAUSE_NEW_ORDERS,
                    payload={},
                    actor="cli",
                    reason="maintenance",
                    idempotency_key="same",
                    at=NOW,
                )


if __name__ == "__main__":
    unittest.main()
