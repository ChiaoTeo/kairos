from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from kairos.ports import Environment
from kairos.application import ApplicationConfig, FixedClock, RuntimePaths
from kairos.orchestration.monitoring import OperationalMonitor


class ApplicationFoundationTests(unittest.TestCase):
    def test_fixed_clock_requires_timezone_and_drives_runtime_timestamps(self) -> None:
        timestamp = datetime(2026, 7, 17, 1, 2, 3, tzinfo=timezone.utc)
        clock = FixedClock(timestamp)
        monitor = OperationalMonitor(clock=clock)
        monitor.disconnected("binance", "test disconnect")
        self.assertEqual(monitor.alerts[0].timestamp, timestamp)

        with self.assertRaises(ValueError):
            FixedClock(datetime(2026, 7, 17))

    def test_application_config_centralizes_and_validates_runtime_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths.under(directory)
            config = ApplicationConfig(
                Environment.TESTNET,
                paths,
                reconciliation_tolerance=Decimal("0.0001"),
            )
            config.validate()
            self.assertEqual(paths.runtime_database, Path(directory) / "runtime" / "runtime.sqlite3")

            invalid = RuntimePaths(
                Path(directory),
                Path(directory) / "catalog.json",
                Path(directory),
                Path(directory).parent / "outside.sqlite3",
                Path(directory) / "artifacts",
            )
            with self.assertRaises(ValueError):
                ApplicationConfig(Environment.TESTNET, invalid).validate()


if __name__ == "__main__":
    unittest.main()
