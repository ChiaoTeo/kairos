from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from kairospy.runtime import LiveRunProcessIdentity, LiveRunRegistry
from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore


NOW = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)


class LiveRunRegistryTests(unittest.TestCase):
    def test_live_run_registry_records_process_identity_and_detects_stale_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            registry = LiveRunRegistry(store)
            identity = LiveRunProcessIdentity.create(
                run_id="live-a",
                runtime_id="runtime-a",
                started_at=NOW,
                config_hash="config-hash",
                version="test-version",
            )

            heartbeat = registry.heartbeat(
                identity,
                observed_state="running",
                desired_state="running",
                state={"services": []},
                at=NOW,
            )

            self.assertEqual(heartbeat.identity.run_id, "live-a")
            self.assertEqual(heartbeat.identity.config_hash, "config-hash")
            self.assertEqual(heartbeat.identity.version, "test-version")

            fresh = registry.status("live-a", at=NOW + timedelta(seconds=2), stale_after_seconds=5)
            stale = registry.status("live-a", at=NOW + timedelta(seconds=6), stale_after_seconds=5)

            assert fresh is not None
            assert stale is not None
            self.assertEqual(fresh["status"], "running")
            self.assertEqual(fresh["stale"], False)
            self.assertEqual(stale["status"], "stale")
            self.assertEqual(stale["stale"], True)
            self.assertEqual(stale["pid"], identity.pid)
            self.assertEqual(stale["host"], identity.host)


if __name__ == "__main__":
    unittest.main()
