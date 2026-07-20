from __future__ import annotations

import subprocess
import sys
import unittest

from kairospy import BacktestRequest, BacktestRunner, Kairos


class KairosApiTests(unittest.TestCase):
    def test_kairospy_package_exports_public_facade(self) -> None:
        from kairospy import Kairos as ImportedKairos

        self.assertIs(Kairos, ImportedKairos)
        request = BacktestRequest(strategy="sma-cross-v1", dataset="fixture:sma-bars-v1")
        self.assertEqual(request.strategy, "sma-cross-v1")
        self.assertTrue(callable(BacktestRunner))

    def test_kairospy_namespace_exports_public_subpackages(self) -> None:
        from kairospy.backtest.synthetic_scenarios import SyntheticScenario
        from kairospy.backtest.feed import (
            InstrumentLifecycleSnapshot,
            MarketSnapshot,
            MarketReplayDataset,
        )
        from kairospy.connectors.massive import MassiveClient
        from kairospy.connectors.massive.close_implied_volatility import OptionCloseImpliedVolatilityPipeline
        from kairospy.application import AsyncServiceSupervisor
        from kairospy.application.runtime_reference_artifact import run_runtime_reference_artifact
        from kairospy.application.runtime_failure_policy import run_runtime_failure_policy
        from kairospy.data import DataProduct, DataProductDefinition, RunMode
        from kairospy.data.market_snapshot_storage import MarketSnapshotStorageDriver
        from kairospy.domain.product import InstrumentContractSpec
        from kairospy.ports.execution import ExecutionPort
        from kairospy.study_platform import open_study
        from kairospy.study_platform.spec import OptionChainCaptureSpec
        from kairospy.backtest.synthetic_scenarios import SyntheticScenario as ImportedSyntheticScenario
        from kairospy.connectors.massive import MassiveClient as ImportedMassiveClient
        from kairospy.connectors.massive.close_implied_volatility import (
            OptionCloseImpliedVolatilityPipeline as ImportedOptionCloseImpliedVolatilityPipeline,
        )
        from kairospy.ports.execution import ExecutionPort as ImportedExecutionPort
        from kairospy.study_platform import open_study as ImportedOpenStudy
        from kairospy.study_platform.spec import OptionChainCaptureSpec as ImportedOptionChainCaptureSpec

        self.assertIs(SyntheticScenario, ImportedSyntheticScenario)
        self.assertTrue(callable(MarketReplayDataset))
        self.assertTrue(callable(InstrumentLifecycleSnapshot))
        self.assertTrue(callable(MarketSnapshot))
        self.assertTrue(callable(MarketSnapshotStorageDriver))
        self.assertTrue(callable(AsyncServiceSupervisor))
        self.assertTrue(callable(run_runtime_reference_artifact))
        self.assertTrue(callable(run_runtime_failure_policy))
        self.assertIs(MassiveClient, ImportedMassiveClient)
        self.assertIs(OptionCloseImpliedVolatilityPipeline, ImportedOptionCloseImpliedVolatilityPipeline)
        self.assertIs(DataProduct, DataProductDefinition)
        self.assertTrue(callable(InstrumentContractSpec))
        self.assertEqual(RunMode.PAPER_TRADING.value, "paper-trading")
        self.assertIs(ExecutionPort, ImportedExecutionPort)
        self.assertIs(open_study, ImportedOpenStudy)
        self.assertIs(OptionChainCaptureSpec, ImportedOptionChainCaptureSpec)

    def test_python_module_entrypoint_uses_kairospy_program_name(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "kairospy", "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("usage: kairospy", completed.stdout)

    def test_python_module_entrypoint_without_command_shows_help(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "kairospy"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("usage: kairospy", completed.stdout)
        self.assertIn("{init,data,features", completed.stdout)
        self.assertEqual(completed.stderr, "")

    def test_daily_ohlcv_cli_names_are_primary_and_day_aggs_are_compatibility_aliases(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "kairospy", "data", "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("prepare-spxw-daily-ohlcv", completed.stdout)
        self.assertIn("prepare-option-daily-ohlcv", completed.stdout)
        self.assertIn("prepare-equity-daily-ohlcv", completed.stdout)
        self.assertIn("compatibility alias for prepare-spxw-daily-ohlcv", completed.stdout)
        self.assertIn("compatibility alias for prepare-option-daily-ohlcv", completed.stdout)
        self.assertIn("compatibility alias for prepare-equity-daily-ohlcv", completed.stdout)


if __name__ == "__main__":
    unittest.main()
