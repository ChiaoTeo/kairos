from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from kairos.data import (
    DataCatalog, DataProductContract, DatasetKey, DatasetLayer, DataProductDefinition, DatasetRelease, DatasetStatus,
    DataPreparationService, DataPromotionPolicyProfile, DatasetQualityService, QualityLevel, DatasetClient,
    STUDY_DEFAULT_POLICY,
    data_promotion_policy_profile,
)
from kairos.storage.data_lake import write_daily_dataset
from kairos.__main__ import main
from contextlib import redirect_stdout
from io import StringIO


class DatasetQualityAssessmentTests(unittest.TestCase):
    def test_complete_point_in_time_ohlcv_is_assessed_q3_and_can_be_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = DataProductDefinition(
                DatasetKey("market.ohlcv.test.daily"), "Test daily OHLCV", DatasetLayer.CANONICAL,
                "Governed daily OHLCV fixture", {"frequency": "1d"}, "period_start", owner="test",
            )
            relative_path = "canonical/ohlcv-release"
            start = datetime(2025, 1, 1, tzinfo=timezone.utc)
            rows = []
            for index in range(400):
                period_start = start + timedelta(days=index)
                period_end = period_start + timedelta(days=1)
                rows.append({
                    "instrument_id": "TEST", "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(), "event_time": period_end.isoformat(),
                    "available_time": period_end.isoformat(), "open": 10, "high": 12,
                    "low": 9, "close": 11, "volume": 100,
                })
            manifest = write_daily_dataset(
                root / relative_path, rows, dataset_id="ohlcv-release",
                schema={"schema_id": "market.ohlcv.v1", "primary_key": ["instrument_id", "period_start"]},
                lineage={"source": {"provider": "fixture"}},
            )
            release = DatasetRelease(
                "ohlcv-release", product.key, "1", "market.ohlcv.v1", "1", "fixture", "1",
                relative_path, "parquet", str(manifest["dataset_sha256"]), "fixture", "test", (),
                DatasetStatus.APPROVED_FOR_RESEARCH, QualityLevel.RESEARCH,
            )
            for name in ("usage", "release"):
                (root / release.relative_path / f"{name}.json").write_text("{}", encoding="utf-8")
            catalog = DataCatalog(root)
            catalog.register_product(product)
            catalog.register_release(release)
            catalog.save()

            assessment = DatasetQualityService(root).assess(release.release_id)
            self.assertTrue(assessment.passed)
            self.assertEqual(assessment.profile, "ohlcv")
            self.assertEqual(assessment.level, QualityLevel.BACKTEST)
            assessed = DataCatalog(root).release(release.release_id)
            self.assertEqual(assessed.quality_level, QualityLevel.BACKTEST)
            prepared = DataPreparationService(DatasetClient(root)).prepare(
                release.release_id, start=start, end=start + timedelta(days=400),
                minimum_quality=QualityLevel.BACKTEST, promote=True,
                actor="test", reason="typed OHLCV quality passed",
            )
            self.assertEqual(prepared.status, DatasetStatus.APPROVED_FOR_BACKTEST)
            self.assertEqual(prepared.release_id, release.release_id)
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--backtest-root", str(root / "backtests"),
                    "backtest", "sma", "--dataset", release.release_id,
                    "--fast", "5", "--slow", "20",
                ]), 0)
                rendered = output.getvalue()
                self.assertIn(release.release_id, rendered)
                self.assertIn('"bars": 400', rendered)
                self.assertIn('"audit_hash"', rendered)
                self.assertEqual(len(tuple((root / "backtests" / "sma").glob("*/manifest.json"))), 1)

    def test_invalid_ohlc_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = DataProductDefinition(
                DatasetKey("market.ohlcv.test.invalid"), "Invalid OHLCV", DatasetLayer.CANONICAL,
                "Invalid fixture", {"frequency": "1d"}, "period_start", owner="test",
            )
            relative_path = "canonical/invalid-release"
            start = datetime(2025, 1, 1, tzinfo=timezone.utc)
            end = start + timedelta(days=1)
            manifest = write_daily_dataset(
                root / relative_path, [{
                    "instrument_id": "TEST", "period_start": start.isoformat(), "period_end": end.isoformat(),
                    "event_time": end.isoformat(), "available_time": end.isoformat(),
                    "open": 10, "high": 8, "low": 9, "close": 11, "volume": 1,
                }], dataset_id="invalid-release",
                schema={"schema_id": "market.ohlcv.v1", "primary_key": ["instrument_id", "period_start"]},
                lineage={"source": {"provider": "fixture"}},
            )
            release = DatasetRelease(
                "invalid-release", product.key, "1", "market.ohlcv.v1", "1", "fixture", "1",
                relative_path, "parquet", str(manifest["dataset_sha256"]),
            )
            catalog = DataCatalog(root); catalog.register_product(product); catalog.register_release(release); catalog.save()
            assessment = DatasetQualityService(root).assess(release.release_id)
            self.assertFalse(assessment.passed)
            self.assertEqual(assessment.level, QualityLevel.ARCHIVED)
            self.assertEqual(DataCatalog(root).release(release.release_id).status, DatasetStatus.QUARANTINED)

    def test_short_but_valid_local_ohlcv_history_is_diagnostic_not_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = DataProductDefinition(
                DatasetKey("market.ohlcv.test.short"), "Short OHLCV", DatasetLayer.CANONICAL,
                "Short but valid local fixture", {"frequency": "1d"}, "period_start", owner="test",
            )
            relative_path = "canonical/short-release"
            start = datetime(2025, 1, 1, tzinfo=timezone.utc)
            end = start + timedelta(days=1)
            manifest = write_daily_dataset(
                root / relative_path, [{
                    "instrument_id": "TEST", "period_start": start.isoformat(), "period_end": end.isoformat(),
                    "event_time": end.isoformat(), "available_time": end.isoformat(),
                    "open": 10, "high": 12, "low": 9, "close": 11, "volume": 1,
                }], dataset_id="short-release",
                schema={"schema_id": "market.ohlcv.v1", "primary_key": ["instrument_id", "period_start"]},
                lineage={"source": {"provider": "fixture"}},
            )
            release = DatasetRelease(
                "short-release", product.key, "1", "market.ohlcv.v1", "1", "fixture", "1",
                relative_path, "parquet", str(manifest["dataset_sha256"]),
            )
            catalog = DataCatalog(root); catalog.register_product(product); catalog.register_release(release); catalog.save()

            assessment = DatasetQualityService(root).assess(release.release_id)
            checks = {item.name: item for item in assessment.checks}

            self.assertTrue(assessment.passed)
            self.assertEqual(assessment.level, QualityLevel.RESEARCH)
            self.assertFalse(checks["backtest_history"].passed)
            self.assertEqual(checks["backtest_history"].severity, "diagnostic")
            self.assertEqual(DataCatalog(root).release(release.release_id).status, DatasetStatus.APPROVED_FOR_RESEARCH)
            prepared = DataPreparationService(DatasetClient(root)).prepare(
                release.release_id, start=start, end=end,
                minimum_quality=QualityLevel.RESEARCH, promote=False,
            )
            self.assertEqual(prepared.policy.diagnostics, ("backtest_history",))
            self.assertTrue(prepared.policy.passed)
            with self.assertRaisesRegex(RuntimeError, "requires Q3"):
                DataPreparationService(DatasetClient(root)).prepare(
                    release.release_id, start=start, end=end,
                    minimum_quality=QualityLevel.BACKTEST, promote=True,
                )

    def test_promotion_policy_can_promote_selected_diagnostics_to_use_gates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = DataProductDefinition(
                DatasetKey("market.ohlcv.test.policy"), "Policy OHLCV", DatasetLayer.CANONICAL,
                "Policy fixture", {"frequency": "1d"}, "period_start", owner="test",
            )
            relative_path = "canonical/policy-release"
            start = datetime(2025, 1, 1, tzinfo=timezone.utc)
            end = start + timedelta(days=1)
            manifest = write_daily_dataset(
                root / relative_path, [{
                    "instrument_id": "TEST", "period_start": start.isoformat(), "period_end": end.isoformat(),
                    "event_time": end.isoformat(), "available_time": end.isoformat(),
                    "open": 10, "high": 12, "low": 9, "close": 11, "volume": 1,
                }], dataset_id="policy-release",
                schema={"schema_id": "market.ohlcv.v1", "primary_key": ["instrument_id", "period_start"]},
                lineage={"source": {"provider": "fixture"}},
            )
            release = DatasetRelease(
                "policy-release", product.key, "1", "market.ohlcv.v1", "1", "fixture", "1",
                relative_path, "parquet", str(manifest["dataset_sha256"]),
            )
            catalog = DataCatalog(root); catalog.register_product(product); catalog.register_release(release); catalog.save()
            strict_research_policy = DataPromotionPolicyProfile(
                "strict-research", QualityLevel.RESEARCH, required_diagnostics=("backtest_history",),
            )

            with self.assertRaisesRegex(RuntimeError, "strict-research"):
                DataPreparationService(DatasetClient(root)).prepare(
                    release.release_id, start=start, end=end,
                    minimum_quality=QualityLevel.RESEARCH, promotion_policy=strict_research_policy,
                )

    def test_product_contract_can_declare_default_promotion_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = DataProductDefinition(
                DatasetKey("market.ohlcv.test.contract-policy"), "Contract Policy OHLCV", DatasetLayer.CANONICAL,
                "Contract policy fixture", {"frequency": "1d"}, "period_start", owner="test",
            )
            relative_path = "canonical/contract-policy-release"
            start = datetime(2025, 1, 1, tzinfo=timezone.utc)
            end = start + timedelta(days=1)
            manifest = write_daily_dataset(
                root / relative_path, [{
                    "instrument_id": "TEST", "period_start": start.isoformat(), "period_end": end.isoformat(),
                    "event_time": end.isoformat(), "available_time": end.isoformat(),
                    "open": 10, "high": 12, "low": 9, "close": 11, "volume": 1,
                }], dataset_id="contract-policy-release",
                schema={"schema_id": "market.ohlcv.v1", "primary_key": ["instrument_id", "period_start"]},
                lineage={"source": {"provider": "fixture"}},
            )
            release = DatasetRelease(
                "contract-policy-release", product.key, "1", "market.ohlcv.v1", "1", "fixture", "1",
                relative_path, "parquet", str(manifest["dataset_sha256"]),
            )
            catalog = DataCatalog(root)
            catalog.register_product_spec(DataProductContract(
                product,
                "canonical/contract-policy",
                release.schema_id,
                {
                    "promotion_policy": {
                        "Q2": {
                            "name": "contract-q2-strict",
                            "minimum_assessment_level": "Q2",
                            "required_diagnostics": ["backtest_history"],
                        }
                    }
                },
                quality_profile="ohlcv",
            ))
            catalog.register_release(release)
            catalog.save()

            with self.assertRaisesRegex(RuntimeError, "contract-q2-strict"):
                DataPreparationService(DatasetClient(root)).prepare(
                    release.release_id, start=start, end=end,
                    minimum_quality=QualityLevel.RESEARCH,
                )

    def test_product_contract_can_reference_builtin_promotion_policy_profile(self) -> None:
        self.assertIs(data_promotion_policy_profile("study-default"), STUDY_DEFAULT_POLICY)
        self.assertIs(data_promotion_policy_profile("research-default"), STUDY_DEFAULT_POLICY)
        self.assertEqual(
            data_promotion_policy_profile("backtest-default").minimum_assessment_level,
            QualityLevel.BACKTEST,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = DataProductDefinition(
                DatasetKey("market.ohlcv.test.builtin-policy"), "Builtin Policy OHLCV", DatasetLayer.CANONICAL,
                "Builtin policy fixture", {"frequency": "1d"}, "period_start", owner="test",
            )
            relative_path = "canonical/builtin-policy-release"
            start = datetime(2025, 1, 1, tzinfo=timezone.utc)
            rows = []
            for index in range(400):
                period_start = start + timedelta(days=index)
                period_end = period_start + timedelta(days=1)
                rows.append({
                    "instrument_id": "TEST", "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(), "event_time": period_end.isoformat(),
                    "available_time": period_end.isoformat(), "open": 10, "high": 12,
                    "low": 9, "close": 11, "volume": 100,
                })
            manifest = write_daily_dataset(
                root / relative_path, rows, dataset_id="builtin-policy-release",
                schema={"schema_id": "market.ohlcv.v1", "primary_key": ["instrument_id", "period_start"]},
                lineage={"source": {"provider": "fixture"}},
            )
            release = DatasetRelease(
                "builtin-policy-release", product.key, "1", "market.ohlcv.v1", "1", "fixture", "1",
                relative_path, "parquet", str(manifest["dataset_sha256"]),
            )
            catalog = DataCatalog(root)
            catalog.register_product_spec(DataProductContract(
                product,
                "canonical/builtin-policy",
                release.schema_id,
                {"promotion_policy": {"Q3": "backtest-default"}},
                quality_profile="ohlcv",
            ))
            catalog.register_release(release)
            catalog.save()

            prepared = DataPreparationService(DatasetClient(root)).prepare(
                release.release_id, start=start, end=start + timedelta(days=400),
                minimum_quality=QualityLevel.BACKTEST, promote=True,
                actor="test", reason="builtin policy profile passed",
            )

            self.assertEqual(prepared.policy.reason, "release builtin-policy-release satisfies Q3 promotion policy")
            self.assertEqual(prepared.status, DatasetStatus.APPROVED_FOR_BACKTEST)


if __name__ == "__main__":
    unittest.main()
