from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from kairos.product_surface import DataProductApi, RunProductApi, StrategyProductApi, StudyProductApi


ROOT = Path(__file__).parents[1]


def command(root: Path, *args: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "kairos", "--format", "json", "--lake-root", str(root), *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


class FourProductSurfaceTests(unittest.TestCase):
    def test_python_product_apis_share_the_same_artifact_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factor_file = root / "factor.py"
            factor_file.write_text("def compute(data):\n    return data['bars']\n", encoding="utf-8")

            data = DataProductApi(root)
            study = StudyProductApi(root)
            strategy = StrategyProductApi(root)
            run = RunProductApi(root)

            downloaded = data.download("tutorial-sma-data")
            study.open("api-study", hypothesis="api path")
            study.add_data(
                "api-study",
                name="bars",
                dataset="market.ohlcv.crypto.tutorial.btc-usdt.1h",
            )
            added_factor = study.add_factor("api-study", name="momentum_12_1", file=factor_file)
            study_lock = study.freeze("api-study", version="1.0.0")
            strategy.open("api-strategy", from_study="api-study@1.0.0")
            strategy.bind_factor("api-strategy", name="primary", study_factor="momentum_12_1")
            strategy_lock = strategy.freeze("api-strategy", version="1.0.0")
            started = run.start_snapshot("api-strategy@1.0.0", mode="backtest")
            replayed = run.replay(started["run_id"])

        self.assertEqual(downloaded["release_id"], "fixture:sma-bars-v1")
        self.assertEqual(study_lock["factors"]["momentum_12_1"]["code_hash"], added_factor["code_hash"])
        self.assertEqual(strategy_lock["inputs"]["primary"]["source_hash"], added_factor["code_hash"])
        self.assertEqual(started["target"]["hash"], strategy_lock["lock_hash"])
        self.assertTrue(replayed["passed"])

    def test_data_study_strategy_run_user_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external-input"
            external.mkdir()
            contract = external / "sentiment.contract.json"
            csv_file = external / "sentiment.csv"
            live_connector = external / "sentiment_live.py"
            factor_file = external / "momentum_factor.py"
            risk_file = external / "risk.json"

            contract.write_text(json.dumps({
                "dataset_id": "reference.sentiment.equity.us",
                "primary_time": "available_time",
                "grain": {"kind": "event_stream"},
                "fields": ["available_time", "instrument_id", "sentiment"],
                "freshness": {"max_age_seconds": 60},
            }), encoding="utf-8")
            csv_file.write_text(
                "available_time,instrument_id,sentiment\n"
                "2026-01-01T00:00:00Z,equity:US:AAPL,0.4\n",
                encoding="utf-8",
            )
            live_connector.write_text(
                "def subscribe(params, context):\n"
                "    yield {'available_time': '2026-01-01T00:00:00Z', 'instrument_id': 'equity:US:AAPL', 'sentiment': 0.4}\n",
                encoding="utf-8",
            )
            factor_file.write_text(
                "def compute(data):\n"
                "    return data['bars']\n",
                encoding="utf-8",
            )
            risk_file.write_text(json.dumps({"max_gross_exposure": 1.0}), encoding="utf-8")

            downloaded = command(root, "data", "download", "tutorial-sma-data")
            written = command(
                root,
                "data",
                "write",
                "--file",
                str(csv_file),
                "--as",
                "reference.sentiment.equity.us",
                "--contract",
                str(contract),
            )
            live_view = command(
                root,
                "data",
                "write",
                "--live",
                "--connector",
                str(live_connector),
                "--as",
                "reference.sentiment.equity.us",
                "--contract",
                str(contract),
            )
            study = command(root, "study", "open", "momentum-study", "--hypothesis", "momentum persists")
            bars = command(
                root,
                "study",
                "add-data",
                "--workspace",
                "momentum-study",
                "--name",
                "bars",
                "--dataset",
                "market.ohlcv.crypto.tutorial.btc-usdt.1h",
            )
            sentiment = command(
                root,
                "study",
                "add-data",
                "--workspace",
                "momentum-study",
                "--name",
                "sentiment",
                "--dataset",
                "reference.sentiment.equity.us",
            )
            factor = command(
                root,
                "study",
                "add-factor",
                "--workspace",
                "momentum-study",
                "--name",
                "momentum_12_1",
                "--file",
                str(factor_file),
            )
            research_run = command(root, "run", "start", "--study", "momentum-study", "--mode", "research")
            study_lock = command(root, "study", "freeze", "momentum-study", "--version", "1.0.0")
            strategy = command(
                root,
                "strategy",
                "open",
                "momentum-long-only",
                "--from-study",
                "momentum-study@1.0.0",
            )
            bound_factor = command(
                root,
                "strategy",
                "bind-factor",
                "--workspace",
                "momentum-long-only",
                "--name",
                "primary",
                "--study-factor",
                "momentum_12_1",
            )
            risk = command(root, "strategy", "set-risk", "momentum-long-only", str(risk_file))
            strategy_lock = command(root, "strategy", "freeze", "momentum-long-only", "--version", "1.0.0")
            backtest_run = command(root, "run", "start", "--snapshot", "momentum-long-only@1.0.0", "--mode", "backtest")
            inspected = command(root, "run", "inspect", "--run-id", backtest_run["run_id"])
            replayed = command(root, "run", "replay", "--run-id", backtest_run["run_id"])
            compared = command(
                root,
                "run",
                "compare",
                "--first",
                research_run["run_id"],
                "--second",
                backtest_run["run_id"],
            )

        self.assertEqual(downloaded["product"], "data")
        self.assertEqual(downloaded["release_id"], "fixture:sma-bars-v1")
        self.assertEqual(written["dataset_id"], "reference.sentiment.equity.us")
        self.assertEqual(written["primary_time"], "available_time")
        self.assertEqual(live_view["kind"], "live_view_manifest")
        self.assertEqual(live_view["live_data_plane"]["channel_contract"], "BoundedEventChannel")
        self.assertEqual(live_view["live_data_plane"]["freshness"]["max_age_seconds"], 60)
        self.assertEqual(study["product"], "study")
        self.assertEqual(bars["release_id"], "fixture:sma-bars-v1")
        self.assertEqual(sentiment["release_id"], written["release_id"])
        self.assertEqual(len(factor["code_hash"]), 64)
        self.assertEqual(study_lock["data"]["bars"]["release_id"], "fixture:sma-bars-v1")
        self.assertEqual(study_lock["factors"]["momentum_12_1"]["code_hash"], factor["code_hash"])
        self.assertEqual(strategy["derived_from"]["lock_hash"], study_lock["lock_hash"])
        self.assertEqual(bound_factor["source_hash"], factor["code_hash"])
        self.assertEqual(len(risk["risk_hash"]), 64)
        self.assertEqual(strategy_lock["inputs"]["primary"]["source_hash"], factor["code_hash"])
        self.assertEqual(backtest_run["target"]["hash"], strategy_lock["lock_hash"])
        self.assertEqual(inspected["run_id"], backtest_run["run_id"])
        self.assertTrue(replayed["passed"])
        self.assertFalse(compared["same_target"])
        self.assertFalse(compared["same_mode"])
        self.assertTrue(Path(backtest_run["manifest"]).exists())
