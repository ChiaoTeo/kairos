from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from kairospy.__main__ import main
from kairospy.data import DatasetKey, DatasetLayer, DataProductDefinition, register_market_replay_dataset
from kairospy.data.market_snapshot_storage import MarketSnapshotStorageDriver
from kairospy.backtest.synthetic_scenarios import build_synthetic_backtest_dataset


class OptionsStudyCliTests(unittest.TestCase):
    @staticmethod
    def _register(root: Path, dataset):
        path = MarketSnapshotStorageDriver(root / "curated").save(dataset)
        product = DataProductDefinition(
            DatasetKey("curated.synthetic.options-study"), "Options study fixture", DatasetLayer.CURATED,
            "Governed synthetic options study fixture", {"synthetic": "true"}, "timestamp", owner="test",
        )
        return register_market_replay_dataset(
            root, dataset, path, product, provider="synthetic", venue="synthetic", synthetic=True,
        )

    def test_pricing_option_prices_and_solves_iv(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["pricing", "option", "--right", "call", "--underlying", "100", "--strike", "100", "--years", "1", "--rate", "0.05", "--volatility", "0.20"])
        self.assertEqual(code, 0)
        self.assertIn("Model: black_scholes", output.getvalue())
        self.assertIn("Delta:", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["pricing", "option", "--right", "call", "--underlying", "100", "--strike", "100", "--years", "1", "--rate", "0.05", "--market-price", "10.45058357"])
        self.assertEqual(code, 0)
        self.assertIn("Solver: converged", output.getvalue())

    def test_dataset_surface_calibration_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = self._register(root, build_synthetic_backtest_dataset())
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["--lake-root", directory, "vol", "calibrate", "--dataset", release.release_id])
        self.assertEqual(code, 0)
        self.assertIn("Surfaces: 4", output.getvalue())
        self.assertIn("Valuation failures:", output.getvalue())

    def test_risk_scenario_outputs_full_revaluation_and_explain(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([
                "risk", "scenario", "--right", "put", "--underlying", "6000", "--strike", "5700",
                "--years", "0.1", "--rate", "0.04", "--volatility", "0.25", "--quantity", "-2",
                "--spot-shock", "-0.10", "--vol-shock", "0.05", "--time-advance-days", "1",
            ])
        self.assertEqual(code, 0)
        self.assertIn("Scenario value:", output.getvalue())
        self.assertIn("Residual:", output.getvalue())

    def test_study_readiness_rejects_synthetic_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dataset = build_synthetic_backtest_dataset()
            release = self._register(Path(directory), dataset)
            output = io.StringIO()
            with redirect_stdout(output):
                code = main([
                    "--lake-root", directory, "study", "readiness",
                    "--dataset", release.release_id,
                ])
        self.assertEqual(code, 2)
        self.assertIn("Conclusion status: DATA_NOT_READY", output.getvalue())
        self.assertIn("FAIL: dataset_is_synthetic", output.getvalue())


if __name__ == "__main__":
    unittest.main()
