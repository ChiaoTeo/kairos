from __future__ import annotations

from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import unittest

from kairos.__main__ import main
from kairos.connectors.massive.equity_daily_ohlcv import MassiveEquityDailyOhlcvPipeline
from kairos.data import DataCatalog, DatasetRelease, DatasetStatus, DatasetStorageKind, QualityLevel
from kairos.data.bootstrap import register_configured_products, register_default_products
from kairos.features.us_equity_momentum import UsEquityMomentumDatasetBuilder, UsEquityMomentumPolicy
from kairos.research_platform import StudyWorkspace, StudyWorkspaceRepository, ensure_sma_tutorial_dataset, open_study
from kairos.research_platform.tutorial_data import tutorial_sma_bars
from tests.test_massive_daily_ohlcv import _EquitySource


class StudySessionTests(unittest.TestCase):
    def test_open_study_is_a_static_public_symbol_for_ide_navigation(self) -> None:
        self.assertEqual(open_study.__module__, "kairos.research_platform.session")

    def _study(self, root: Path):
        release = ensure_sma_tutorial_dataset(root)
        bars = tutorial_sma_bars()
        StudyWorkspaceRepository(root).create(StudyWorkspace(
            "btc-sma-first", "1.0.0", "SMA spread may predict direction",
            release.release_id, str(release.content_hash), "available_time",
            bars[0].end.isoformat(), (bars[-1].end + timedelta(hours=1)).isoformat(),
        ))
        return open_study("btc-sma-first", root=root)

    def test_study_opens_bound_release_as_dataframe_without_manual_bar_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            study = self._study(Path(directory))
            frame = study.data.pandas()
            selected = study.data.pandas(columns=("available_time", "close"))

        self.assertEqual(frame.shape, (90, 10))
        self.assertEqual(tuple(selected.columns), ("available_time", "close"))
        self.assertIsInstance(frame.iloc[0]["close"], Decimal)
        self.assertEqual(frame.iloc[0]["close"], Decimal("100"))

    def test_describe_can_render_pretty_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            study = self._study(Path(directory))

            description = study.describe()
            table = study.describe(format="prettytable")
            direct_table = study.describe_table()

        self.assertEqual(description["rows"], 90)
        self.assertIn("| Field", table)
        self.assertIn("| Study ID", table)
        self.assertIn("| Rows", table)
        self.assertIn("90", table)
        self.assertEqual(table, direct_table)

    def test_profile_and_scaffold_are_safe_and_repeatable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            study = self._study(Path(directory))
            profile = study.profile()
            first = study.scaffold()
            second = study.scaffold()

            self.assertTrue(profile.passed)
            self.assertEqual(profile.rows, 90)
            self.assertEqual(profile.missing_values, 0)
            self.assertEqual(profile.duplicate_primary_times, 0)
            self.assertTrue(profile.valid_ohlc)
            self.assertTrue(profile.point_in_time_safe)
            self.assertEqual(first, second)
            self.assertIn("study.data.pandas()", first.read_text(encoding="utf-8"))

            first.write_text("# user research\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "user changes"):
                study.scaffold()

    def test_workspace_rejects_dataset_content_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = ensure_sma_tutorial_dataset(root)
            bars = tutorial_sma_bars()
            StudyWorkspaceRepository(root).create(StudyWorkspace(
                "drift", "1.0.0", "drift must fail", release.release_id, "f" * 64,
                "available_time", bars[0].end.isoformat(), (bars[-1].end + timedelta(hours=1)).isoformat(),
            ))
            with self.assertRaisesRegex(ValueError, "hash does not match"):
                open_study("drift", root=root)

    def test_legacy_tutorial_workspace_is_auditably_migrated_before_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            StudyWorkspaceRepository(root).create(StudyWorkspace(
                "legacy", "1.0.0", "legacy tutorial",
                "fixture:sma-bars-v1", "f9c8e187e9fcf1b4565f6dbb6903155ce2e0ae06a1dc1ef7960720d6052c174e",
                "available_time", "2026-01-01T00:00:00+00:00", "2026-01-04T18:00:00+00:00",
            ))
            study = open_study("legacy", root=root)
            workspace_dir = root/"study-workspaces"/"legacy"/"1.0.0"

            self.assertEqual(study.data.arrow().num_rows, 90)
            self.assertNotEqual(study.workspace.input_content_hash, "f9c8e187e9fcf1b4565f6dbb6903155ce2e0ae06a1dc1ef7960720d6052c174e")
            self.assertTrue((workspace_dir/"workspace.pre-input-migration.json").exists())
            self.assertTrue((workspace_dir/"input_migration.json").exists())

    def test_us_equity_momentum_study_start_fixes_q3_feature_release_and_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_equity_source(root)
            UsEquityMomentumDatasetBuilder(root).build_from_ohlcv_directory(
                "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=raw/dataset=demo",
                dataset_id="demo-momentum",
                policy=UsEquityMomentumPolicy(minimum_adv20=Decimal("1"), minimum_history=2),
            )

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "study", "plan", "us-equity-momentum",
                    "--dataset", "features.momentum.equity.us.1d",
                    "--start", "2026-01-02T00:00:00+00:00",
                    "--end", "2026-01-07T00:00:00+00:00",
                ]), 0)
                plan = json.loads(output.getvalue())
            self.assertTrue(plan["ready"])
            self.assertEqual(len(plan["required_releases"]), 4)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "study", "start", "us-equity-momentum",
                    "--dataset", "features.momentum.equity.us.1d",
                    "--start", "2026-01-02T00:00:00+00:00",
                    "--end", "2026-01-07T00:00:00+00:00",
                ]), 0)
                started = json.loads(output.getvalue())

            self.assertEqual(started["dataset"], "features.momentum.equity.us.1d")
            self.assertEqual(started["quality_level"], "Q3")
            workspace_dir = root / "study-workspaces" / "us-equity-momentum" / "1.0.0"
            self.assertTrue((workspace_dir / "workspace.json").exists())
            self.assertTrue((workspace_dir / "input_releases.json").exists())
            inputs = json.loads((workspace_dir / "input_releases.json").read_text())
            self.assertEqual({item["logical_key"] for item in inputs}, {
                "market.returns.equity.us.1d",
                "market.universe.equity.us.1d",
                "features.liquidity.equity.us.1d",
                "features.momentum.equity.us.1d",
            })
            study = open_study("us-equity-momentum", root=root)
            self.assertEqual(study.describe()["rows"], 3)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "data", "us-equity-momentum-diagnostics",
                    "--study-id", "us-equity-momentum",
                ]), 0)
                readiness = json.loads(output.getvalue())
            self.assertTrue(readiness["ready_for_study"])
            self.assertFalse(readiness["ready_for_backtest"])
            self.assertEqual(readiness["summary"]["errors"], 0)
            self.assertGreaterEqual(readiness["summary"]["warnings"], 1)

    def test_us_equity_momentum_diagnostics_reports_universe_missing_reason_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_equity_source(root, values=((date(2026, 1, 2), 100), (date(2026, 1, 6), 121)))
            UsEquityMomentumDatasetBuilder(root).build_from_ohlcv_directory(
                "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=raw/dataset=demo",
                dataset_id="demo-momentum",
                policy=UsEquityMomentumPolicy(minimum_adv20=Decimal("1"), minimum_history=2),
            )

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "data", "us-equity-momentum-diagnostics",
                    "--study-id", "us-equity-momentum",
                ]), 0)
                readiness = json.loads(output.getvalue())

            check = next(item for item in readiness["checks"] if item["code"] == "universe_missing_status")
            self.assertFalse(check["passed"])
            self.assertEqual(check["severity"], "warning")
            self.assertEqual(check["evidence"]["missing_bar_rows"], 1)
            self.assertEqual(check["evidence"]["missing_reason_counts"]["expected_trading_session_without_bar"], 1)

    def test_us_equity_momentum_study_start_rejects_unregistered_corporate_action_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_equity_source(root)
            actions = root / "reference/provider=massive/corporate_actions/manual/version=test"
            actions.mkdir(parents=True)
            (actions / "events.json").write_text(json.dumps([{
                "instrument_id": "equity:us:NVDA",
                "effective_at": {"$datetime": "2026-01-06T00:00:00+00:00"},
                "ratio": {"$decimal": "2"},
            }]), encoding="utf-8")
            UsEquityMomentumDatasetBuilder(root).build_from_ohlcv_directory(
                "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=raw/dataset=demo",
                dataset_id="demo-momentum",
                policy=UsEquityMomentumPolicy(minimum_adv20=Decimal("1"), minimum_history=2),
                corporate_actions_directory=actions,
            )

            with self.assertRaisesRegex(RuntimeError, "no matching reference.corporate_actions"):
                main([
                    "--lake-root", directory,
                    "study", "start", "us-equity-momentum",
                    "--dataset", "features.momentum.equity.us.1d",
                    "--start", "2026-01-02T00:00:00+00:00",
                    "--end", "2026-01-07T00:00:00+00:00",
                ])

    def test_prepare_us_equity_momentum_one_command_uses_existing_raw_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline = MassiveEquityDailyOhlcvPipeline(root, client=object())
            pipeline.source = _EquitySource(root, rows=[
                {"t": 1767330000000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 100000, "n": 5, "vw": 100},
                {"t": 1767589200000, "o": 110, "h": 111, "l": 109, "c": 110, "v": 100000, "n": 5, "vw": 110},
                {"t": 1767675600000, "o": 121, "h": 122, "l": 120, "c": 121, "v": 100000, "n": 5, "vw": 121},
            ])
            manifest = pipeline.prepare("raw.oneclick", "NVDA", date(2026, 1, 2), date(2026, 1, 7), view="raw")
            register_default_products(root)
            catalog = DataCatalog(root)
            product = catalog.product("market.ohlcv.equity.us.massive.1d.raw")
            catalog.register_release(DatasetRelease(
                "raw-oneclick-release",
                product.key,
                "1",
                "market.ohlcv.equity.us.1d.v1",
                "1",
                "massive.equity_daily_ohlcv",
                "1",
                "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=raw/dataset=raw.oneclick",
                "parquet",
                str(manifest["content_sha256"]),
                "massive",
                "us-securities",
                ("market.ohlcv.equity.us.massive.1d.raw@latest-research",),
                DatasetStatus.APPROVED_FOR_RESEARCH,
                QualityLevel.RESEARCH,
                datetime.now(timezone.utc).isoformat(),
                DatasetStorageKind.TABULAR,
                "1",
            ))
            catalog.save()

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory,
                    "data", "prepare-us-equity-momentum",
                    "--raw-dataset", "market.ohlcv.equity.us.massive.1d.raw",
                    "--start", "2026-01-02T00:00:00+00:00",
                    "--end", "2026-01-07T00:00:00+00:00",
                    "--dataset-id", "demo-momentum",
                    "--minimum-adv20", "1",
                    "--minimum-history", "2",
                ]), 0)
                result = json.loads(output.getvalue())

            self.assertEqual(result["workflow"], "us-equity-momentum")
            self.assertEqual(result["raw_releases"][0]["release_id"], "raw-oneclick-release")
            self.assertTrue(result["ready_for_study"])
            self.assertFalse(result["ready_for_backtest"])
            self.assertEqual(result["readiness"]["summary"]["errors"], 0)
            workspace = root / "study-workspaces" / "us-equity-momentum" / "1.0.0" / "input_releases.json"
            self.assertTrue(workspace.exists())

    def test_prepare_us_equity_momentum_one_command_combines_multiple_configured_raw_releases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "connectors.json"
            nvda_key = "market.ohlcv.equity.us.massive.nvda.1d.raw"
            aapl_key = "market.ohlcv.equity.us.massive.aapl.1d.raw"
            config.write_text(json.dumps({"massive_equity_products": [
                {"logical_key": nvda_key, "ticker": "NVDA", "view": "raw"},
                {"logical_key": aapl_key, "ticker": "AAPL", "view": "raw"},
            ]}), encoding="utf-8")
            register_default_products(root)
            register_configured_products(root, config)

            for ticker, key in (("NVDA", nvda_key), ("AAPL", aapl_key)):
                pipeline = MassiveEquityDailyOhlcvPipeline(root, client=object())
                pipeline.source = _EquitySource(root, rows=[
                    {"t": 1767330000000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 100000, "n": 5, "vw": 100},
                    {"t": 1767589200000, "o": 110, "h": 111, "l": 109, "c": 110, "v": 100000, "n": 5, "vw": 110},
                    {"t": 1767675600000, "o": 121, "h": 122, "l": 120, "c": 121, "v": 100000, "n": 5, "vw": 121},
                ])
                manifest = pipeline.prepare(f"raw.{ticker.lower()}", ticker, date(2026, 1, 2), date(2026, 1, 7), view="raw")
                catalog = DataCatalog(root)
                product = catalog.product(key)
                catalog.register_release(DatasetRelease(
                    f"raw-{ticker.lower()}-release",
                    product.key,
                    "1",
                    "market.ohlcv.equity.us.1d.v1",
                    "1",
                    "massive.equity_daily_ohlcv",
                    "1",
                    f"canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=raw/dataset=raw.{ticker.lower()}",
                    "parquet",
                    str(manifest["content_sha256"]),
                    "massive",
                    "us-securities",
                    (f"{key}@latest-research",),
                    DatasetStatus.APPROVED_FOR_RESEARCH,
                    QualityLevel.RESEARCH,
                    datetime.now(timezone.utc).isoformat(),
                    DatasetStorageKind.TABULAR,
                    "1",
                ))
                catalog.save()

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory,
                    "data", "prepare-us-equity-momentum",
                    "--raw-dataset", nvda_key,
                    "--raw-dataset", aapl_key,
                    "--connector-config", str(config),
                    "--start", "2026-01-02T00:00:00+00:00",
                    "--end", "2026-01-07T00:00:00+00:00",
                    "--dataset-id", "demo-momentum",
                    "--minimum-adv20", "1",
                    "--minimum-history", "2",
                ]), 0)
                result = json.loads(output.getvalue())

            self.assertEqual(len(result["raw_releases"]), 2)
            self.assertEqual(result["features"]["outputs"]["momentum"]["rows"], 6)
            self.assertEqual(result["readiness"]["checks"][0]["evidence"]["missing"], [])

    def test_prepare_us_equity_momentum_one_command_can_sync_corporate_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline = MassiveEquityDailyOhlcvPipeline(root, client=object())
            pipeline.source = _EquitySource(root, rows=[
                {"t": 1767330000000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 100000, "n": 5, "vw": 100},
                {"t": 1767589200000, "o": 110, "h": 111, "l": 109, "c": 110, "v": 100000, "n": 5, "vw": 110},
                {"t": 1767675600000, "o": 60, "h": 61, "l": 59, "c": 60, "v": 200000, "n": 5, "vw": 60},
            ])
            manifest = pipeline.prepare("raw.actions", "NVDA", date(2026, 1, 2), date(2026, 1, 7), view="raw")
            register_default_products(root)
            catalog = DataCatalog(root)
            product = catalog.product("market.ohlcv.equity.us.massive.1d.raw")
            catalog.register_release(DatasetRelease(
                "raw-actions-release",
                product.key,
                "1",
                "market.ohlcv.equity.us.1d.v1",
                "1",
                "massive.equity_daily_ohlcv",
                "1",
                "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=raw/dataset=raw.actions",
                "parquet",
                str(manifest["content_sha256"]),
                "massive",
                "us-securities",
                ("market.ohlcv.equity.us.massive.1d.raw@latest-research",),
                DatasetStatus.APPROVED_FOR_RESEARCH,
                QualityLevel.RESEARCH,
                datetime.now(timezone.utc).isoformat(),
                DatasetStorageKind.TABULAR,
                "1",
            ))
            catalog.save()

            with (
                patch("kairos.__main__.MassiveConfig.from_env", return_value=object()),
                patch("kairos.__main__.MassiveVendorArchiveClient", return_value=_CorporateActionArchive(root)),
                StringIO() as output,
                redirect_stdout(output),
            ):
                self.assertEqual(main([
                    "--lake-root", directory,
                    "data", "prepare-us-equity-momentum",
                    "--raw-dataset", "market.ohlcv.equity.us.massive.1d.raw",
                    "--start", "2026-01-02T00:00:00+00:00",
                    "--end", "2026-01-07T00:00:00+00:00",
                    "--dataset-id", "demo-momentum",
                    "--minimum-adv20", "1",
                    "--minimum-history", "2",
                    "--sync-corporate-actions",
                ]), 0)
                result = json.loads(output.getvalue())

            self.assertTrue(result["corporate_actions"]["synced"])
            self.assertTrue(result["corporate_actions"]["release_id"].startswith("corpact_"))
            self.assertTrue(result["corporate_actions"]["quality_passed"])
            self.assertEqual(result["corporate_actions"]["quality_level"], "Q2")
            self.assertEqual(result["corporate_actions"]["event_count"], 2)
            self.assertTrue((root / result["corporate_actions"]["directory"] / "events.json").exists())
            self.assertEqual(
                DataCatalog(root).release(result["corporate_actions"]["release_id"]).product_key.value,
                "reference.corporate_actions.equity.us.massive",
            )
            workspace = root / "study-workspaces" / "us-equity-momentum" / "1.0.0" / "input_releases.json"
            inputs = json.loads(workspace.read_text(encoding="utf-8"))
            self.assertIn("reference.corporate_actions.equity.us.massive", {item["logical_key"] for item in inputs})
            action_check = next(item for item in result["readiness"]["checks"] if item["code"] == "corporate_action_release")
            self.assertTrue(action_check["passed"])
            self.assertEqual(action_check["evidence"]["corporate_actions_release_id"], result["corporate_actions"]["release_id"])
            returns = result["features"]["outputs"]["returns"]
            import pyarrow.parquet as pq
            rows = pq.read_table(
                root
                / f"curated/market/returns/asset_class=equity/region=us/interval=1d/dataset={returns['release_id']}"
                / returns["file"]
            ).to_pylist()
            self.assertEqual(rows[2]["split_ratio"], Decimal("2"))
            self.assertEqual(rows[2]["cash_dividend"], Decimal("1"))

    def test_prepare_us_equity_momentum_auto_uses_clean_identity_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline = MassiveEquityDailyOhlcvPipeline(root, client=object())
            pipeline.source = _EquitySource(root, rows=[
                {"t": 1767330000000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 100000, "n": 5, "vw": 100},
                {"t": 1767675600000, "o": 121, "h": 122, "l": 120, "c": 121, "v": 100000, "n": 5, "vw": 121},
            ])
            manifest = pipeline.prepare("raw.identity", "NVDA", date(2026, 1, 2), date(2026, 1, 7), view="raw")
            register_default_products(root)
            catalog = DataCatalog(root)
            product = catalog.product("market.ohlcv.equity.us.massive.1d.raw")
            catalog.register_release(DatasetRelease(
                "raw-identity-release",
                product.key,
                "1",
                "market.ohlcv.equity.us.1d.v1",
                "1",
                "massive.equity_daily_ohlcv",
                "1",
                "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=raw/dataset=raw.identity",
                "parquet",
                str(manifest["content_sha256"]),
                "massive",
                "us-securities",
                ("market.ohlcv.equity.us.massive.1d.raw@latest-research",),
                DatasetStatus.APPROVED_FOR_RESEARCH,
                QualityLevel.RESEARCH,
                datetime.now(timezone.utc).isoformat(),
                DatasetStorageKind.TABULAR,
                "1",
            ))
            catalog.save()
            reference = root / "reference/provider=massive/equity_identity/version=clean"
            reference.mkdir(parents=True)
            (reference / "manifest.json").write_text(json.dumps({
                "sha256": "clean",
                "mapping_count": 1,
                "instrument_count": 1,
                "quarantine_count": 0,
            }), encoding="utf-8")
            (reference / "instruments.json").write_text(json.dumps([{
                "instrument_id": "equity:us:NVDA",
                "listing_date": "1999-01-22",
                "delisting_date": "2026-01-05",
                "security_type": "CS",
                "active": False,
            }]), encoding="utf-8")
            (reference / "mappings.json").write_text(json.dumps([{
                "provider_id": "massive",
                "namespace": "stocks",
                "external_id": "NVDA",
                "target_type": "instrument",
                "target_id": "equity:us:NVDA",
                "effective_from": "1999-01-22T00:00:00+00:00",
                "effective_to": None,
            }]), encoding="utf-8")
            (reference / "quarantine.json").write_text("[]", encoding="utf-8")

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory,
                    "data", "prepare-us-equity-momentum",
                    "--raw-dataset", "market.ohlcv.equity.us.massive.1d.raw",
                    "--start", "2026-01-02T00:00:00+00:00",
                    "--end", "2026-01-07T00:00:00+00:00",
                    "--dataset-id", "demo-momentum",
                    "--minimum-adv20", "1",
                    "--minimum-history", "2",
                ]), 0)
                result = json.loads(output.getvalue())

            self.assertTrue(result["reference"]["auto_detected"])
            self.assertTrue(result["reference"]["release_id"].startswith("identity_"))
            self.assertEqual(result["features"]["reference"]["directory"], "reference/provider=massive/equity_identity/version=clean")
            workspace = root / "study-workspaces" / "us-equity-momentum" / "1.0.0" / "input_releases.json"
            inputs = json.loads(workspace.read_text(encoding="utf-8"))
            self.assertIn("reference.identity.equity.us.massive", {item["logical_key"] for item in inputs})
            identity_check = next(item for item in result["readiness"]["checks"] if item["code"] == "identity_reference_release")
            self.assertTrue(identity_check["passed"])
            self.assertEqual(identity_check["evidence"]["identity_release_id"], result["reference"]["release_id"])
            import pyarrow.parquet as pq
            universe = result["features"]["outputs"]["universe"]
            rows = pq.read_table(
                root
                / f"curated/market/universe/asset_class=equity/region=us/frequency=1d/dataset={universe['release_id']}"
                / universe["file"]
            ).to_pylist()
            self.assertEqual(rows[1]["missing_reason"], "delisted_after_reference_end")


def _write_equity_source(root: Path, values=((date(2026, 1, 2), 100), (date(2026, 1, 5), 110), (date(2026, 1, 6), 121))) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    source = root / "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=raw/dataset=demo"
    source.mkdir(parents=True)
    rows = []
    for value, close in values:
        rows.append({
            "ticker": "NVDA",
            "instrument_id": "equity:us:NVDA",
            "price_view": "raw",
            "event_date": value,
            "window_start": datetime(value.year, value.month, value.day, tzinfo=timezone.utc),
            "available_time": datetime(value.year, value.month, value.day, 21, tzinfo=timezone.utc),
            "open": Decimal(str(close)),
            "high": Decimal(str(close + 1)),
            "low": Decimal(str(close - 1)),
            "close": Decimal(str(close)),
            "volume": Decimal("100000"),
            "transactions": 5,
            "vwap": Decimal(str(close)),
        })
    pq.write_table(pa.Table.from_pylist(rows), source / "part-demo.parquet")
    (source / "manifest.json").write_text(json.dumps({"content_sha256": "demo-source"}), encoding="utf-8")


class _CorporateActionArchive:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls = 0

    def fetch_pages(self, resource, params, max_pages=100000):
        self.calls += 1
        directory = self.root / "source/provider=massive/resource=fake-corporate-actions" / f"request_id={self.calls}"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "receipt.json").write_text(json.dumps({"status": "complete", "resource": resource}), encoding="utf-8")
        return SimpleNamespace(directory=directory, resource=resource, params=dict(params))

    def iter_results(self, archive):
        resource = archive.resource
        params = archive.params
        ticker = str(params["ticker"])
        if resource == "/v3/reference/splits":
            yield {
                "id": "split-1",
                "ticker": ticker,
                "execution_date": "2026-01-06",
                "split_from": "1",
                "split_to": "2",
            }
        elif resource == "/v3/reference/dividends":
            yield {
                "id": "dividend-1",
                "ticker": ticker,
                "ex_dividend_date": "2026-01-06",
                "pay_date": "2026-01-20",
                "cash_amount": "1",
                "currency": "USD",
            }


if __name__ == "__main__":
    unittest.main()
