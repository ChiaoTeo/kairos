from pathlib import Path
import tempfile
import unittest

from kairospy import BacktestRequest, BacktestRunner, Kairos


class KairosBacktestApiTest(unittest.TestCase):
    def test_kairospy_backtest_returns_notebook_friendly_result_view(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = Kairos(root).backtest(
                strategy="sma-cross-v1@1.2.0",
                dataset="fixture:sma-bars-v1",
                parameters={"fast": 5, "slow": 15},
                artifact_root=root / "artifacts",
            )
            summary = result.summary()
            explanation = result.explain(at="2026-01-02T00:00:00Z")

        self.assertEqual(summary["mode"], "backtest")
        self.assertEqual(summary["bars"], 90)
        self.assertEqual(len(summary["audit_hash"]), 64)
        self.assertEqual(result.trades()["count"], summary["trades"])
        self.assertEqual(result.equity()["final_equity"], summary["final_equity"])
        self.assertIsNotNone(explanation["factor"])
        self.assertIsNotNone(explanation["decision"])

    def test_backtest_runner_accepts_structured_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request = BacktestRequest(
                strategy="sma-cross-v1",
                dataset="fixture:sma-bars-v1",
                parameters={"fast": 5, "slow": 15},
            )
            summary = BacktestRunner(directory).run(request).summary()
        self.assertEqual(summary["input_identity"], "fixture:sma-bars-v1")


if __name__ == "__main__":
    unittest.main()
