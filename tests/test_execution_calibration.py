from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from examples.runtime.sma_historical_simulation import run
from trading.execution import build_execution_calibration_release, load_execution_calibration_release


ROOT = Path(__file__).parents[1]


class ExecutionCalibrationTests(unittest.TestCase):
    def test_runtime_fills_publish_calibration_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "run"
            simulation = asyncio.run(run(run_root))
            release = build_execution_calibration_release(
                run_root / "runtime" / "runtime.sqlite3", root / "calibration",
                venue="simulated", environment="testnet", strategy_id="sma-cross-v1",
            )
            loaded = load_execution_calibration_release(release.manifest_path)
            manifest = json.loads(release.manifest_path.read_text())

        self.assertEqual(manifest["kind"], "execution_calibration_release")
        self.assertEqual(loaded.release_hash, manifest["release_hash"])
        self.assertEqual(manifest["sample_count"], simulation["fills"])
        self.assertEqual(manifest["strategy_id"], "sma-cross-v1")
        self.assertEqual(manifest["summary"]["fill_ratio"]["count"], simulation["fills"])
        self.assertEqual(len(manifest["release_hash"]), 64)

    def test_runtime_calibration_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "run"
            simulation = asyncio.run(run(run_root))
            completed = subprocess.run(
                [sys.executable, "-m", "trading", "runtime", "calibrate-execution",
                 "--db", str(run_root / "runtime" / "runtime.sqlite3"), "--output-root", str(root / "calibration"),
                 "--venue", "simulated", "--environment", "testnet", "--strategy", "sma-cross-v1"],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            payload = json.loads(completed.stdout)
            manifest = json.loads(Path(payload["manifest"]).read_text())

        self.assertEqual(payload["sample_count"], simulation["fills"])
        self.assertEqual(payload["release_hash"], manifest["release_hash"])

    def test_calibration_manifest_rejects_tampered_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "run"
            asyncio.run(run(run_root))
            release = build_execution_calibration_release(
                run_root / "runtime" / "runtime.sqlite3", root / "calibration",
                venue="simulated", environment="testnet", strategy_id="sma-cross-v1",
            )
            manifest = json.loads(release.manifest_path.read_text())
            manifest["sample_count"] = manifest["sample_count"] + 1
            release.manifest_path.write_text(json.dumps(manifest))

            with self.assertRaises(ValueError):
                load_execution_calibration_release(release.manifest_path)


if __name__ == "__main__":
    unittest.main()
