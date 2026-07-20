from __future__ import annotations

import subprocess
import sys
import unittest

from kairos import BacktestRequest, BacktestRunner, Kairos


class KairosApiTests(unittest.TestCase):
    def test_kairos_package_exports_public_facade(self) -> None:
        from kairos import Kairos as TradingKairos

        self.assertIs(Kairos, TradingKairos)
        request = BacktestRequest(strategy="sma-cross-v1", dataset="fixture:sma-bars-v1")
        self.assertEqual(request.strategy, "sma-cross-v1")
        self.assertTrue(callable(BacktestRunner))

    def test_kairos_namespace_aliases_trading_subpackages(self) -> None:
        from kairos.backtest.synthetic_scenarios import SyntheticScenario
        from kairos.backtest.feed import (
            InstrumentLifecycleSnapshot,
            MarketSnapshot,
            MarketReplayDataset,
        )
        from kairos.connectors.massive import MassiveClient
        from kairos.connectors.massive.close_implied_volatility import OptionCloseImpliedVolatilityPipeline
        from kairos.application import AsyncServiceSupervisor
        from kairos.application.runtime_reference_artifact import run_runtime_reference_artifact
        from kairos.application.runtime_failure_policy import run_runtime_failure_policy
        from kairos.data import DataProduct, DataProductDefinition, RunMode
        from kairos.data.market_snapshot_storage import MarketSnapshotStorageDriver
        from kairos.domain.product import InstrumentContractSpec
        from kairos.ports.execution import ExecutionPort
        from kairos.research_platform import open_study
        from kairos.research_platform.spec import OptionChainCaptureSpec
        from kairos.backtest.synthetic_scenarios import SyntheticScenario as TradingSyntheticScenario
        from kairos.connectors.massive import MassiveClient as TradingMassiveClient
        from kairos.connectors.massive.close_implied_volatility import (
            OptionCloseImpliedVolatilityPipeline as TradingOptionCloseImpliedVolatilityPipeline,
        )
        from kairos.ports.execution import ExecutionPort as TradingExecutionPort
        from kairos.research import open_study as TradingOpenStudy
        from kairos.research.spec import OptionChainCaptureSpec as TradingOptionChainCaptureSpec

        self.assertIs(SyntheticScenario, TradingSyntheticScenario)
        self.assertTrue(callable(MarketReplayDataset))
        self.assertTrue(callable(InstrumentLifecycleSnapshot))
        self.assertTrue(callable(MarketSnapshot))
        self.assertTrue(callable(MarketSnapshotStorageDriver))
        self.assertTrue(callable(AsyncServiceSupervisor))
        self.assertTrue(callable(run_runtime_reference_artifact))
        self.assertTrue(callable(run_runtime_failure_policy))
        self.assertIs(MassiveClient, TradingMassiveClient)
        self.assertIs(OptionCloseImpliedVolatilityPipeline, TradingOptionCloseImpliedVolatilityPipeline)
        self.assertIs(DataProduct, DataProductDefinition)
        self.assertTrue(callable(InstrumentContractSpec))
        self.assertEqual(RunMode.PAPER_TRADING.value, "paper-trading")
        self.assertIs(ExecutionPort, TradingExecutionPort)
        self.assertIs(open_study, TradingOpenStudy)
        self.assertIs(OptionChainCaptureSpec, TradingOptionChainCaptureSpec)

    def test_python_module_entrypoint_uses_kairos_program_name(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "kairos", "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("usage: kairos", completed.stdout)

    def test_daily_ohlcv_cli_names_are_primary_and_day_aggs_are_compatibility_aliases(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "kairos", "data", "--help"],
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
