from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from kairospy.backtest.engine import BacktestEngine
from kairospy.backtest.synthetic_scenarios import SyntheticScenario, assess_dataset, build_synthetic_backtest_dataset
from kairospy.backtest.repository import BacktestRepository
from kairospy.backtest.result import BacktestConfig, ResultStatus
from kairospy.backtest.experiment_runner import BacktestExperimentRunner
from kairospy.risk.limits import RiskLimits
from kairospy.strategies.bull_put_spread import BullPutSpreadConfig, BullPutSpreadStrategy
from kairospy.__main__ import main
from kairospy.data.market_snapshot_storage import MarketSnapshotStorageDriver
from kairospy.backtest.calendar import AlwaysOpenCalendar, CalendarRegistry, TradingCalendar
from kairospy.domain.product import ProductType
from kairospy.data import DatasetKey, DatasetLayer, DataProductDefinition, register_market_replay_dataset
from datetime import date, time


def run_scenario(scenario=SyntheticScenario.PROFIT_TARGET, *, strategy_config=BullPutSpreadConfig(), minimum=Decimal("0.95")):
    dataset = build_synthetic_backtest_dataset(scenario)
    config = BacktestConfig(dataset.manifest.start, dataset.manifest.end, minimum_data_coverage=minimum)
    return BacktestEngine(dataset, config, BullPutSpreadStrategy(strategy_config)).run()


class BacktestEngineTests(unittest.TestCase):
    def test_profit_target_is_deterministic_and_matches_hand_calculation(self) -> None:
        first = run_scenario()
        second = run_scenario()
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(first.intents, second.intents)
        self.assertEqual(first.orders, second.orders)
        self.assertEqual(first.fills, second.fills)
        self.assertEqual(first.equity, second.equity)
        self.assertEqual(first.status, ResultStatus.VALID)
        self.assertEqual(first.metrics["final_equity"], Decimal("100207.28"))
        self.assertEqual(len(first.fills), 2)
        self.assertGreater(first.orders[0].eligible_at, first.orders[0].created_at)
        self.assertEqual(first.metrics["cash_reconciliation_difference"], Decimal("0"))
        self.assertIn("1", first.metrics["pnl_by_entry_dte"])
        self.assertIn("15:00", first.metrics["pnl_by_entry_hour"])
        self.assertIn("medium", first.metrics["pnl_by_iv_regime"])

    def test_no_trade_and_never_filled(self) -> None:
        no_trade = run_scenario(SyntheticScenario.NO_TRADE)
        self.assertFalse(no_trade.fills)
        never = run_scenario(SyntheticScenario.NEVER_FILLED)
        self.assertFalse(never.fills)
        self.assertGreater(never.metrics["unfilled_orders"], 0)

    def test_stop_loss_loses_money(self) -> None:
        result = run_scenario(SyntheticScenario.STOP_LOSS)
        self.assertEqual(len(result.fills), 2)
        self.assertLess(result.metrics["final_equity"], Decimal("100000"))

    def test_fee_can_turn_gross_edge_negative(self) -> None:
        strategy = BullPutSpreadConfig(profit_target=Decimal("0"))
        result = run_scenario(SyntheticScenario.FEE_TURNS_PROFIT_TO_LOSS, strategy_config=strategy)
        self.assertEqual(len(result.fills), 2)
        self.assertLess(result.metrics["final_equity"], Decimal("100000"))

    def test_missing_quote_and_force_close_failure_are_not_valid(self) -> None:
        missing = run_scenario(SyntheticScenario.MISSING_QUOTE, minimum=Decimal("0.90"))
        self.assertNotEqual(missing.status, ResultStatus.VALID)
        failed = run_scenario(SyntheticScenario.FORCE_CLOSE_FAILURE, minimum=Decimal("0.90"))
        self.assertEqual(failed.status, ResultStatus.INVALID)
        self.assertTrue(any(reason.startswith("force_close_failed") for reason in failed.validity_reasons))

    def test_suite_persists_conservative_and_stress_and_replay_hash_is_stable(self) -> None:
        dataset = build_synthetic_backtest_dataset()
        config = BacktestConfig(dataset.manifest.start, dataset.manifest.end)
        with tempfile.TemporaryDirectory() as directory:
            repository = BacktestRepository(directory)
            conservative, stress = BacktestExperimentRunner(repository).run_suite(dataset, config, BullPutSpreadConfig(), RiskLimits())
            self.assertLessEqual(stress.metrics["final_equity"], conservative.metrics["final_equity"])
            for result in (conservative, stress):
                run_dir = repository.run_dir(result.run_id)
                self.assertEqual(
                    {path.name for path in run_dir.iterdir()},
                    {"manifest.json", "config.json", "intents.jsonl", "risk_decisions.jsonl", "orders.jsonl", "fills.jsonl", "settlements.jsonl", "strategy_decisions.jsonl", "positions.csv", "equity.csv", "trades.csv", "metrics.json", "summary.md"},
                )
                manifest = repository.load_manifest(result.run_id)
                self.assertEqual(manifest["audit_hash"], repository.audit_hash(run_dir))
                self.assertTrue(manifest["synthetic_dataset"])
                self.assertIn("Synthetic fixture", (run_dir / "summary.md").read_text())

    def test_backtest_cli_run_show_replay_and_compare(self) -> None:
        import io
        with tempfile.TemporaryDirectory() as directory:
            dataset_root = f"{directory}/datasets"
            backtest_root = f"{directory}/backtests"
            dataset = build_synthetic_backtest_dataset()
            directory_path = MarketSnapshotStorageDriver(dataset_root).save(dataset)
            release = register_market_replay_dataset(directory, dataset, directory_path,
                DataProductDefinition(DatasetKey("curated.synthetic.profit_target.development"), "Synthetic development",
                               DatasetLayer.CURATED, primary_time="timestamp"),
                provider="synthetic", venue="synthetic", synthetic=True)
            release_directory = Path(directory) / release.relative_path
            data_release_manifest = json.loads((release_directory / "data_release_manifest.json").read_text(encoding="utf-8"))
            release_metadata = json.loads((release_directory / "release.json").read_text(encoding="utf-8"))
            self.assertEqual(data_release_manifest["kind"], "data_release_manifest")
            self.assertEqual(data_release_manifest["content_hash"], release.content_hash)
            self.assertEqual(len(release_metadata["data_release_manifest_hash"]), 64)
            self.assertEqual(release_metadata["artifact_ref"], f"data://{release.product_key}/releases/{release.release_id}")
            common = ["--lake-root", directory, "--dataset-root", dataset_root, "--backtest-root", backtest_root, "backtest"]
            with io.StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([*common, "run", "--dataset", dataset.manifest.dataset_id]), 0)
                self.assertIn("conservative:", output.getvalue())
                self.assertIn("stress:", output.getvalue())
            repository = BacktestRepository(backtest_root)
            manifests = list(repository.root.glob("*/*/manifest.json"))
            self.assertEqual(len(manifests), 2)
            run_ids = [path.parent.name for path in manifests]
            with io.StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([*common, "show", "--run-id", run_ids[0]]), 0)
                self.assertIn("Status: valid", output.getvalue())
            with io.StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([*common, "replay", "--run-id", run_ids[0]]), 0)
                self.assertIn("Replay: MATCH", output.getvalue())
            with io.StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([*common, "compare", "--run-id", run_ids[0], "--run-id", run_ids[1]]), 0)
                self.assertIn("slippage=", output.getvalue())
            validation = build_synthetic_backtest_dataset(split="validation")
            test = build_synthetic_backtest_dataset(split="test")
            for item in (validation, test):
                path = MarketSnapshotStorageDriver(dataset_root).save(item)
                register_market_replay_dataset(directory, item, path,
                    DataProductDefinition(DatasetKey(f"curated.synthetic.profit_target.{item.manifest.split}"),
                                   f"Synthetic {item.manifest.split}", DatasetLayer.CURATED, primary_time="timestamp"),
                    provider="synthetic", venue="synthetic", synthetic=True)
            with io.StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    *common, "validate",
                    "--development", dataset.manifest.dataset_id,
                    "--validation", validation.manifest.dataset_id,
                    "--test", test.manifest.dataset_id,
                ]), 0)
                self.assertIn("Parameters were frozen", output.getvalue())

    def test_dataset_readiness_and_splits(self) -> None:
        import json
        from pathlib import Path
        hashes = set()
        for split in ("development", "validation", "test"):
            dataset = build_synthetic_backtest_dataset(split=split)
            readiness = assess_dataset(dataset)
            self.assertTrue(readiness.ready)
            self.assertEqual(dataset.manifest.split, split)
            hashes.add(dataset.manifest.content_hash)
        self.assertEqual(len(hashes), 1, "split labels must not alter market content hash")
        scenarios = json.loads((Path(__file__).parent / "fixtures" / "backtest" / "scenarios.json").read_text())["scenarios"]
        self.assertEqual(set(scenarios), {item.value for item in SyntheticScenario})
        schema = json.loads((Path(__file__).parent / "fixtures" / "backtest" / "dataset.schema.json").read_text())
        self.assertEqual(schema["properties"]["manifest"]["properties"]["schema_version"]["const"], 1)

    def test_trading_calendar_handles_weekends_and_explicit_holidays(self) -> None:
        calendar = TradingCalendar(holidays=frozenset({date(2025, 1, 1)}))
        self.assertFalse(calendar.is_trading_day(date(2025, 1, 1)))
        self.assertFalse(calendar.is_trading_day(date(2025, 1, 4)))
        self.assertTrue(calendar.is_trading_day(date(2025, 1, 2)))
        self.assertFalse(calendar.is_trading_day(date(2025, 7, 4)))
        self.assertFalse(calendar.is_trading_day(date(2025, 4, 18)))
        self.assertEqual(calendar.session(date(2025, 11, 28)).closes_at.timetz().replace(tzinfo=None), time(13))
        self.assertEqual(calendar.dte(date(2025, 1, 3), date(2025, 1, 6)), 1)
        with self.assertRaises(ValueError):
            calendar.session(date(2025, 1, 4))
        crypto = CalendarRegistry(calendar).for_product(ProductType.CRYPTO_SPOT)
        self.assertIsInstance(crypto, AlwaysOpenCalendar)
        self.assertEqual(crypto.dte(date(2025, 1, 3), date(2025, 1, 6)), 3)

    def test_frozen_parameter_split_validation_records_all_trials(self) -> None:
        import json
        datasets = tuple(build_synthetic_backtest_dataset(split=split) for split in ("development", "validation", "test"))
        config = BacktestConfig(datasets[0].manifest.start, datasets[0].manifest.end)
        with tempfile.TemporaryDirectory() as directory:
            service = BacktestExperimentRunner(BacktestRepository(directory))
            output = service.validate_splits(datasets, config, BullPutSpreadConfig(), RiskLimits())
            summary = json.loads((output / "validation-summary.json").read_text())
            self.assertTrue(summary["parameters_frozen"])
            self.assertTrue(summary["synthetic"])
            self.assertEqual(len(summary["results"]), 6)
            self.assertEqual(len({row["run_id"] for row in summary["results"]}), 6)
            self.assertEqual({row["split"] for row in summary["results"]}, {"development", "validation", "test"})
            self.assertEqual({row["model"] for row in summary["results"]}, {"conservative", "stress"})


if __name__ == "__main__":
    unittest.main()
