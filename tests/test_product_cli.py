from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal

from kairospy.product_surface import DataProductApi
from kairospy.product_workflow import _write_binance_spot_bar_capture


ROOT = Path(__file__).parents[1]


def command(root: Path, *args: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "kairospy", "--format", "json", "--lake-root", str(root), *args],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    return json.loads(completed.stdout)


class ProductCliTests(unittest.TestCase):
    def test_study_add_factor_accepts_metadata_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factor_file = root / "factor.py"
            metadata_file = root / "factor.metadata.json"
            factor_file.write_text("def compute(inputs, params, context):\n    return inputs['bars']\n", encoding="utf-8")
            metadata_file.write_text(json.dumps({
                "inputs": ["bars"],
                "parameters": {"fast": 5, "slow": 20},
                "primary_time": "available_time",
                "fields": ["instrument_id", "available_time", "signal"],
                "point_in_time": True,
                "dependencies": [],
            }), encoding="utf-8")

            command(root, "data", "download", "tutorial-sma-data")
            command(root, "study", "open", "cli-factor-study")
            command(root, "study", "add-data", "--workspace", "cli-factor-study", "--name", "bars",
                    "--dataset", "market.ohlcv.crypto.tutorial.btc-usdt.1h")
            added = command(root, "study", "add-factor", "--workspace", "cli-factor-study",
                            "--name", "sma_signal", "--file", str(factor_file),
                            "--metadata", str(metadata_file))
            lock = command(root, "study", "freeze", "cli-factor-study", "--version", "1.0.0")
            duplicate = subprocess.run(
                [sys.executable, "-m", "kairospy", "--format", "json", "--lake-root", str(root),
                 "study", "freeze", "cli-factor-study", "--version", "1.0.0"],
                cwd=ROOT, check=False, capture_output=True, text=True,
            )
            workspace_manifest = json.loads(
                (root / "studies" / "cli-factor-study" / "workspace.json").read_text(encoding="utf-8")
            )
            lifecycle_events = [
                json.loads(line)
                for line in (root / "studies" / "cli-factor-study" / "lifecycle.jsonl").read_text(encoding="utf-8").splitlines()
                if line
            ]

        self.assertEqual(added["metadata_status"], "declared")
        self.assertEqual(added["runtime"]["network_allowed"], False)
        self.assertEqual(added["runtime"]["filesystem_access"], "study_inputs_only")
        self.assertEqual(len(added["factor_contract_hash"]), 64)
        self.assertEqual(lock["factors"]["sma_signal"]["parameters_hash"], added["parameters_hash"])
        self.assertEqual(lock["factors"]["sma_signal"]["runtime"], added["runtime"])
        self.assertEqual(duplicate.returncode, 2)
        self.assertIn("Study Lock version already exists", json.loads(duplicate.stderr)["error"]["message"])
        self.assertEqual(workspace_manifest["latest_lock_hash"], lock["lock_hash"])
        self.assertEqual([item["event"] for item in lifecycle_events], ["opened", "frozen"])

    def test_study_factor_run_writes_profile_from_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factor_file = root / "factor.py"
            metadata_file = root / "factor.metadata.json"
            factor_file.write_text(
                "def compute(inputs, params, context):\n"
                "    rows = inputs['bars'].rows(columns=('available_time', 'close'))\n"
                "    return [{'available_time': rows[0]['available_time'], 'signal': rows[0]['close']}]\n",
                encoding="utf-8",
            )
            metadata_file.write_text(json.dumps({
                "inputs": ["bars"],
                "parameters": {},
                "primary_time": "available_time",
                "fields": ["available_time", "signal"],
                "point_in_time": True,
            }), encoding="utf-8")

            command(root, "data", "download", "tutorial-sma-data")
            command(root, "study", "open", "cli-factor-run-study")
            command(root, "study", "add-data", "--workspace", "cli-factor-run-study", "--name", "bars",
                    "--dataset", "market.ohlcv.crypto.tutorial.btc-usdt.1h")
            command(root, "study", "add-factor", "--workspace", "cli-factor-run-study", "--name", "signal",
                    "--file", str(factor_file), "--metadata", str(metadata_file))
            result = command(root, "study", "factor-run", "cli-factor-run-study", "signal")
            published = command(root, "study", "publish-factor", "cli-factor-run-study", "signal",
                                "--as", "features.cli.signal")
            profile = json.loads(Path(result["profile"]).read_text(encoding="utf-8"))

        self.assertEqual(result["operation"], "factor-run")
        self.assertEqual(result["row_count"], 1)
        self.assertTrue(profile["passed"])
        self.assertEqual(published["operation"], "publish-factor")
        self.assertEqual(published["dataset_id"], "features.cli.signal")
        self.assertEqual(published["factor_run_hash"], result["run_hash"])
        self.assertEqual(published["factor_output_hash"], result["factor_output_hash"])
        self.assertEqual(profile["factor_output_hash"], result["factor_output_hash"])
        self.assertEqual(len(published["quality_report_hash"]), 64)

    def test_strategy_set_model_code_accepts_metadata_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_file = root / "model.py"
            metadata_file = root / "model.metadata.json"
            model_file.write_text(
                "def decide(context):\n"
                "    rows = context.data('bars').rows(columns=('available_time', 'close'))\n"
                "    return {'intent': 'hold', 'close': rows[0]['close']}\n",
                encoding="utf-8",
            )
            metadata_file.write_text(json.dumps({
                "inputs": ["bars"],
                "intent_schema": {"kind": "target_exposure"},
                "side_effects_allowed": False,
            }), encoding="utf-8")

            command(root, "data", "download", "tutorial-sma-data")
            command(root, "study", "open", "cli-model-study")
            command(root, "study", "add-data", "--workspace", "cli-model-study", "--name", "bars",
                    "--dataset", "market.ohlcv.crypto.tutorial.btc-usdt.1h")
            command(root, "study", "freeze", "cli-model-study", "--version", "1.0.0")
            command(root, "strategy", "open", "cli-model-strategy", "--from-study", "cli-model-study@1.0.0")
            model = command(root, "strategy", "set-model-code", "cli-model-strategy", str(model_file),
                            "--metadata", str(metadata_file))
            model_file.write_text("def decide(context):\n    raise RuntimeError('draft file should not run')\n", encoding="utf-8")
            lock = command(root, "strategy", "freeze", "cli-model-strategy", "--version", "1.0.0")
            duplicate = subprocess.run(
                [sys.executable, "-m", "kairospy", "--format", "json", "--lake-root", str(root),
                 "strategy", "freeze", "cli-model-strategy", "--version", "1.0.0"],
                cwd=ROOT, check=False, capture_output=True, text=True,
            )
            old_artifact = Path(model["artifact_path"])
            model_file.write_text("def decide(context):\n    return {'intent': 'dirty-draft'}\n", encoding="utf-8")
            edited_model = command(root, "strategy", "set-model-code", "cli-model-strategy", str(model_file),
                                   "--metadata", str(metadata_file))
            started = command(root, "run", "start", "--snapshot", "cli-model-strategy@1.0.0",
                              "--mode", "backtest", "--execute-strategy")
            decision = json.loads(Path(started["outputs"]["strategy_decision"]).read_text(encoding="utf-8"))
            run_snapshot = json.loads(Path(started["target"]["snapshot_artifact"]).read_text(encoding="utf-8"))
            snapshot_artifact_exists = Path(started["target"]["snapshot_artifact"]).exists()
            old_artifact_exists = old_artifact.exists()

        self.assertEqual(model["operation"], "set-model-code")
        self.assertEqual(len(model["model_contract_hash"]), 64)
        self.assertTrue(old_artifact_exists)
        self.assertNotEqual(edited_model["artifact_path"], model["artifact_path"])
        self.assertEqual(lock["model"]["model_contract_hash"], model["model_contract_hash"])
        self.assertEqual(duplicate.returncode, 2)
        self.assertIn("Strategy Lock version already exists", json.loads(duplicate.stderr)["error"]["message"])
        self.assertEqual(started["target"]["snapshot_hash"], lock["lock_hash"])
        self.assertTrue(started["target"]["snapshot_source"].endswith("strategies/cli-model-strategy/locks/1.0.0/strategy.lock.json"))
        self.assertTrue(started["target"]["workspace_source"].endswith("strategies/cli-model-strategy/strategy.json"))
        self.assertTrue(started["target"]["workspace_dirty"])
        self.assertTrue(snapshot_artifact_exists)
        self.assertEqual(run_snapshot["model"]["artifact_path"], model["artifact_path"])
        self.assertEqual(started["runtime_contract"]["strategy_decision_execution"]["decision_hash"], decision["decision_hash"])
        self.assertEqual(decision["decision"]["close"], "100")

    def test_published_factor_can_feed_strategy_model_from_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factor_file = root / "factor.py"
            factor_metadata = root / "factor.metadata.json"
            model_file = root / "model.py"
            model_metadata = root / "model.metadata.json"
            factor_file.write_text(
                "def compute(inputs, params, context):\n"
                "    rows = inputs['bars'].rows(columns=('available_time', 'close'))\n"
                "    return [{'available_time': rows[0]['available_time'], 'signal': rows[0]['close']}]\n",
                encoding="utf-8",
            )
            factor_metadata.write_text(json.dumps({
                "inputs": ["bars"],
                "parameters": {},
                "primary_time": "available_time",
                "fields": ["available_time", "signal"],
                "point_in_time": True,
            }), encoding="utf-8")
            model_file.write_text(
                "def decide(context):\n"
                "    rows = context.input('primary').rows(columns=('signal',))\n"
                "    return {'intent': 'factor-input', 'signal': rows[0]['signal']}\n",
                encoding="utf-8",
            )
            model_metadata.write_text(json.dumps({
                "inputs": ["primary"],
                "intent_schema": {"kind": "factor_signal"},
                "side_effects_allowed": False,
            }), encoding="utf-8")

            command(root, "data", "download", "tutorial-sma-data")
            command(root, "study", "open", "cli-published-factor-study")
            command(root, "study", "add-data", "--workspace", "cli-published-factor-study", "--name", "bars",
                    "--dataset", "market.ohlcv.crypto.tutorial.btc-usdt.1h")
            command(root, "study", "add-factor", "--workspace", "cli-published-factor-study", "--name", "signal",
                    "--file", str(factor_file), "--metadata", str(factor_metadata))
            factor_run = command(root, "study", "factor-run", "cli-published-factor-study", "signal")
            published = command(root, "study", "publish-factor", "cli-published-factor-study", "signal",
                                "--as", "features.cli.strategy.signal")
            command(root, "study", "freeze", "cli-published-factor-study", "--version", "1.0.0")
            command(root, "strategy", "open", "cli-published-factor-strategy",
                    "--from-study", "cli-published-factor-study@1.0.0")
            bound = command(root, "strategy", "bind-factor", "--workspace", "cli-published-factor-strategy",
                            "--name", "primary", "--study-factor", "signal")
            command(root, "strategy", "set-model-code", "cli-published-factor-strategy", str(model_file),
                    "--metadata", str(model_metadata))
            command(root, "strategy", "freeze", "cli-published-factor-strategy", "--version", "1.0.0")
            started = command(root, "run", "start", "--snapshot", "cli-published-factor-strategy@1.0.0",
                              "--mode", "backtest", "--execute-strategy")
            decision = json.loads(Path(started["outputs"]["strategy_decision"]).read_text(encoding="utf-8"))

        self.assertEqual(bound["materialization_status"], "published_feature")
        self.assertEqual(bound["release_id"], published["release_id"])
        self.assertEqual(bound["factor_run_hash"], factor_run["run_hash"])
        self.assertEqual(decision["decision"]["signal"], 100)

    def test_strategy_model_failure_writes_data_plane_diagnostic_from_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factor_file = root / "factor.py"
            factor_metadata = root / "factor.metadata.json"
            model_file = root / "model.py"
            model_metadata = root / "model.metadata.json"
            factor_file.write_text("def compute(inputs, params, context):\n    return []\n", encoding="utf-8")
            factor_metadata.write_text(json.dumps({
                "inputs": ["bars"],
                "parameters": {},
                "primary_time": "available_time",
                "fields": ["available_time", "signal"],
                "point_in_time": True,
            }), encoding="utf-8")
            model_file.write_text(
                "def decide(context):\n"
                "    return {'rows': context.input('primary').rows()}\n",
                encoding="utf-8",
            )
            model_metadata.write_text(json.dumps({
                "inputs": ["primary"],
                "intent_schema": {"kind": "diagnostic"},
                "side_effects_allowed": False,
            }), encoding="utf-8")

            command(root, "data", "download", "tutorial-sma-data")
            command(root, "study", "open", "cli-diagnostic-study")
            command(root, "study", "add-data", "--workspace", "cli-diagnostic-study", "--name", "bars",
                    "--dataset", "market.ohlcv.crypto.tutorial.btc-usdt.1h")
            command(root, "study", "add-factor", "--workspace", "cli-diagnostic-study", "--name", "signal",
                    "--file", str(factor_file), "--metadata", str(factor_metadata))
            command(root, "study", "freeze", "cli-diagnostic-study", "--version", "1.0.0")
            command(root, "strategy", "open", "cli-diagnostic-strategy",
                    "--from-study", "cli-diagnostic-study@1.0.0")
            bound = command(root, "strategy", "bind-factor", "--workspace", "cli-diagnostic-strategy",
                            "--name", "primary", "--study-factor", "signal")
            command(root, "strategy", "set-model-code", "cli-diagnostic-strategy", str(model_file),
                    "--metadata", str(model_metadata))
            command(root, "strategy", "freeze", "cli-diagnostic-strategy", "--version", "1.0.0")
            failed = subprocess.run(
                [sys.executable, "-m", "kairospy", "--format", "json", "--lake-root", str(root),
                 "run", "start", "--snapshot", "cli-diagnostic-strategy@1.0.0",
                 "--mode", "backtest", "--execute-strategy"],
                cwd=ROOT, check=False, capture_output=True, text=True,
            )
            diagnostics = list((root / "runs" / "cli-diagnostic-strategy").glob("*/diagnostics/data_plane.json"))
            diagnostic = json.loads(diagnostics[0].read_text(encoding="utf-8"))
            error = json.loads(failed.stderr)
            primary_input = next(item for item in diagnostic["inputs"] if item["name"] == "primary")

        self.assertEqual(bound["materialization_status"], "contract_only")
        self.assertEqual(failed.returncode, 2)
        self.assertIn("data plane diagnostic", error["error"]["message"])
        self.assertEqual(diagnostic["issue"], "factor_runtime_missing_input")
        self.assertEqual(primary_input["materialization_status"], "contract_only")
        self.assertIn("publish-factor", diagnostic["next_action"])

    def test_registered_python_provider_download_from_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_dir = root / "provider"
            spec_dir.mkdir()
            provider = spec_dir / "provider.py"
            contract = spec_dir / "macro.contract.json"
            spec = spec_dir / "macro.download.json"
            provider.write_text(
                "from pathlib import Path\n"
                "def acquire(product, scope, context):\n"
                "    count_path = Path(__file__).with_name('calls.txt')\n"
                "    count = int(count_path.read_text()) if count_path.exists() else 0\n"
                "    count_path.write_text(str(count + 1))\n"
                "    token = context['credentials']['KAIROSPY_TEST_CLI_PROVIDER_TOKEN']\n"
                "    value = 4.2 if token == 'cli-secret-token' else -1\n"
                "    return {'rows': [{'available_time': scope['as_of'], 'metric': product['metric'], 'value': value}]}\n",
                encoding="utf-8",
            )
            contract.write_text(json.dumps({
                "dataset_id": "reference.macro.provider",
                "primary_time": "available_time",
                "fields": ["available_time", "metric", "value"],
            }), encoding="utf-8")
            spec.write_text(json.dumps({
                "kind": "data.download",
                "mode": {"acquire_missing": True},
                "source": {
                    "kind": "python_provider",
                    "path": "provider.py",
                    "function": "acquire",
                    "credentials": {"required_env": ["KAIROSPY_TEST_CLI_PROVIDER_TOKEN"]},
                },
                "scope": {"as_of": "2026-01-02T00:00:00Z"},
                "products": [{
                    "dataset_id": "reference.macro.provider",
                    "metric": "cpi",
                    "contract": "macro.contract.json",
                }],
            }), encoding="utf-8")

            registered = command(root, "data", "register-download", "--key", "provider-macro", "--spec", str(spec))
            original_token = os.environ.pop("KAIROSPY_TEST_CLI_PROVIDER_TOKEN", None)
            try:
                os.environ["KAIROSPY_TEST_CLI_PROVIDER_TOKEN"] = "cli-secret-token"
                downloaded = command(root, "data", "download", "provider-macro")
                rows = DataProductApi(root).dataset("reference.macro.provider").rows(columns=("metric", "value"))
                os.environ.pop("KAIROSPY_TEST_CLI_PROVIDER_TOKEN", None)
                reused = command(root, "data", "download", "provider-macro")
                provider_calls = (spec_dir / "calls.txt").read_text(encoding="utf-8")
            finally:
                if original_token is None:
                    os.environ.pop("KAIROSPY_TEST_CLI_PROVIDER_TOKEN", None)
                else:
                    os.environ["KAIROSPY_TEST_CLI_PROVIDER_TOKEN"] = original_token

        self.assertEqual(registered["key"], "provider-macro")
        self.assertEqual(downloaded["source"]["kind"], "python_provider")
        self.assertEqual(downloaded["source"]["function"], "acquire")
        self.assertEqual(len(downloaded["source"]["provider_code_hash"]), 64)
        self.assertEqual(downloaded["source"]["credentials"]["required_env"], ["KAIROSPY_TEST_CLI_PROVIDER_TOKEN"])
        self.assertEqual(rows[0]["metric"], "cpi")
        self.assertEqual(rows[0]["value"], 4.2)
        self.assertEqual(reused["release_id"], downloaded["release_id"])
        self.assertEqual(reused["source"]["acquire_policy"], "reused_existing_release")
        self.assertEqual(provider_calls, "1")
        self.assertNotIn("cli-secret-token", json.dumps(downloaded))

    def test_registered_provider_catalog_from_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider_dir = root / "providers"
            download_dir = root / "downloads"
            provider_dir.mkdir()
            download_dir.mkdir()
            provider = provider_dir / "provider.py"
            provider_spec = provider_dir / "provider.json"
            contract = download_dir / "provider.contract.json"
            download_spec = download_dir / "provider.download.json"
            provider.write_text(
                "def acquire(product, scope, context):\n"
                "    return [{'available_time': scope['as_of'], 'symbol': product['symbol'], 'score': 9}]\n",
                encoding="utf-8",
            )
            provider_spec.write_text(json.dumps({
                "kind": "data.provider",
                "source": {"kind": "python_provider", "path": "provider.py"},
            }), encoding="utf-8")
            contract.write_text(json.dumps({
                "dataset_id": "reference.provider.cli",
                "primary_time": "available_time",
                "fields": ["available_time", "symbol", "score"],
            }), encoding="utf-8")
            download_spec.write_text(json.dumps({
                "kind": "data.download",
                "scope": {"as_of": "2026-01-04T00:00:00Z"},
                "source": {"provider": "cli-provider"},
                "products": [{
                    "dataset_id": "reference.provider.cli",
                    "symbol": "MSFT",
                    "contract": "provider.contract.json",
                }],
            }), encoding="utf-8")

            registered_provider = command(root, "data", "register-provider", "--name", "cli-provider", "--spec", str(provider_spec))
            command(root, "data", "register-download", "--key", "cli-provider-data", "--spec", str(download_spec))
            downloaded = command(root, "data", "download", "cli-provider-data")
            rows = DataProductApi(root).dataset("reference.provider.cli").rows(columns=("symbol", "score"))

        self.assertEqual(registered_provider["operation"], "register-provider")
        self.assertEqual(downloaded["source"]["provider"], "cli-provider")
        self.assertEqual(rows[0]["symbol"], "MSFT")
        self.assertEqual(rows[0]["score"], 9)

    def test_provider_config_credentials_from_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath("kairos.toml").write_text(
                "[project]\nname = \"cli-provider-config\"\n\n"
                "[providers.test]\napi_key = \"env:KAIROSPY_TEST_CLI_CONFIG_PROVIDER_TOKEN\"\n",
                encoding="utf-8",
            )
            provider = root / "provider.py"
            contract = root / "config-provider.contract.json"
            spec = root / "config-provider.download.json"
            provider.write_text(
                "def acquire(product, scope, context):\n"
                "    token = context['credentials']['providers.test.api_key']\n"
                "    value = 13 if token == 'cli-config-secret-token' else -1\n"
                "    return [{'available_time': scope['as_of'], 'symbol': product['symbol'], 'value': value}]\n",
                encoding="utf-8",
            )
            contract.write_text(json.dumps({
                "dataset_id": "reference.provider.config.cli",
                "primary_time": "available_time",
                "fields": ["available_time", "symbol", "value"],
            }), encoding="utf-8")
            spec.write_text(json.dumps({
                "kind": "data.download",
                "scope": {"as_of": "2026-01-06T00:00:00Z"},
                "source": {
                    "kind": "python_provider",
                    "path": "provider.py",
                    "credentials": {"config": ["providers.test.api_key"]},
                },
                "products": [{
                    "dataset_id": "reference.provider.config.cli",
                    "symbol": "GOOG",
                    "contract": "config-provider.contract.json",
                }],
            }), encoding="utf-8")

            command(root, "data", "register-download", "--key", "cli-config-provider", "--spec", str(spec))
            original_token = os.environ.pop("KAIROSPY_TEST_CLI_CONFIG_PROVIDER_TOKEN", None)
            try:
                os.environ["KAIROSPY_TEST_CLI_CONFIG_PROVIDER_TOKEN"] = "cli-config-secret-token"
                downloaded = command(root, "data", "download", "cli-config-provider")
                rows = DataProductApi(root).dataset("reference.provider.config.cli").rows(columns=("symbol", "value"))
            finally:
                if original_token is None:
                    os.environ.pop("KAIROSPY_TEST_CLI_CONFIG_PROVIDER_TOKEN", None)
                else:
                    os.environ["KAIROSPY_TEST_CLI_CONFIG_PROVIDER_TOKEN"] = original_token

        self.assertEqual(downloaded["source"]["credentials"]["required_config"], ["providers.test.api_key"])
        self.assertEqual(rows[0]["symbol"], "GOOG")
        self.assertEqual(rows[0]["value"], 13)
        self.assertNotIn("cli-config-secret-token", json.dumps(downloaded))

    def test_product_cli_defaults_to_localized_text_and_keeps_json_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = [sys.executable, "-m", "kairospy", "--lake-root", str(root)]
            chinese = subprocess.run(
                [*base, "--lang", "zh-CN", "factor", "verify-sma", "--fixture", "--fast", "5", "--slow", "15"],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            english = subprocess.run(
                [*base, "--lang", "en-US", "factor", "verify-sma", "--fixture", "--fast", "5", "--slow", "15"],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            machine = subprocess.run(
                [*base, "--format", "json", "factor", "verify-sma", "--fixture", "--fast", "5", "--slow", "15"],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            quiet = subprocess.run(
                [*base, "--quiet", "factor", "verify-sma", "--fixture", "--fast", "5", "--slow", "15"],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            failed = subprocess.run(
                [*base, "--lang", "zh-CN", "factor", "verify-sma", "--fast", "5", "--slow", "15"],
                cwd=ROOT, check=False, capture_output=True, text=True,
            )

        self.assertIn("SMA 因子验证", chinese.stdout)
        self.assertIn("批量/事件一致性", chinese.stdout)
        self.assertNotIn('{"bars"', chinese.stdout)
        self.assertIn("SMA factor validation", english.stdout)
        self.assertEqual(json.loads(machine.stdout)["bars"], 90)
        self.assertEqual(quiet.stdout, "")
        self.assertEqual(failed.returncode, 2)
        self.assertIn("命令执行失败", failed.stderr)
        self.assertIn("INPUT_DATASET_REQUIRED", failed.stderr)

    def test_sma_tutorial_and_dataset_inference_remove_governance_boilerplate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            completed = subprocess.run(
                [sys.executable, "-m", "kairospy", "--format", "json", "tutorial", "sma", "--output-root", str(root)],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            tutorial = json.loads(completed.stdout)
            repeated = subprocess.run(
                [sys.executable, "-m", "kairospy", "--format", "json", "tutorial", "sma", "--output-root", str(root)],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            created = command(root, "study", "create", "short-study", "--hypothesis", "SMA trend",
                              "--dataset", "fixture:sma-bars-v1")
            inspected = command(root, "study", "inspect", "btc-sma-first")
            preview = command(root, "study", "data", "btc-sma-first", "--head", "3", "--column", "available_time",
                              "--column", "close")
            profile = command(root, "study", "profile", "btc-sma-first")
            scaffold = command(root, "study", "scaffold", "btc-sma-first")
            scaffold_exists = Path(scaffold["script"]).exists()

        self.assertTrue(tutorial["created"])
        self.assertFalse(json.loads(repeated.stdout)["created"])
        self.assertEqual(tutorial["dataset"], "fixture:sma-bars-v1")
        self.assertEqual(len(tutorial["input_hash"]), 64)
        self.assertIn("study data", tutorial["next"])
        self.assertEqual(created["input_release"], "fixture:sma-bars-v1")
        self.assertEqual(created["input_hash"], tutorial["input_hash"])
        self.assertEqual(created["primary_time"], "available_time")
        self.assertEqual(inspected["rows"], 90)
        self.assertEqual(preview["shown"], 3)
        self.assertEqual(preview["columns"], ["available_time", "close"])
        self.assertTrue(profile["passed"])
        self.assertTrue(scaffold_exists)

    def test_builtin_multi_asset_releases_can_be_registered_inspected_and_run(self)->None:
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);registered=command(root,"strategy","register-builtins")
            inspected=command(root,"strategy","inspect","covered-call-v1","--version","1.1.0")
            status=command(root,"strategy","status","covered-call-v1","--version","1.1.0")
            active=command(root,"strategy","activate","covered-call-v1","--version","1.1.0","--actor","operator","--reason","acceptance")
            iron=command(root,"strategy","register-btc-iron-condor","--study-spec-hash","b"*64)
            iron_status=command(root,"strategy","status","btc-iron-condor-v1","--version","1.2.0")
            result=command(root,"run","reference","--strategy","covered-call")
        self.assertGreaterEqual(registered["count"],5);self.assertIn("CoveredCallStrategy",inspected["implementation"]["import_path"])
        self.assertTrue(status["complete"]);self.assertEqual(active["active_version"],"1.1.0")
        self.assertEqual(iron["version"],"1.2.0");self.assertTrue(iron_status["complete"])
        self.assertTrue(result["replay_equal"]);self.assertTrue(result["stress_is_worse"])

    def test_scenarios_one_through_four_run_from_product_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = command(root, "study", "create", "btc-sma", "--hypothesis", "SMA trend",
                "--input-release", "fixture:sma-bars-v1", "--input-hash", "a"*64,
                "--start", "2025-01-01T00:00:00Z", "--end", "2026-01-01T00:00:00Z")
            frozen = command(root, "study", "freeze", "btc-sma")
            factor = command(root, "factor", "register-sma", "--input-identity", "fixture:sma-bars-v1",
                "--fast", "5", "--slow", "15")
            verified = command(root, "factor", "verify-sma", "--fixture", "--fast", "5", "--slow", "15")
            strategy = command(root, "strategy", "register-sma", "--input-identity", "fixture:sma-bars-v1",
                "--fast", "5", "--slow", "15")
            backtest = command(root, "run", "backtest", "--strategy", "sma-cross-v1@1.2.0",
                "--fixture", "--fast", "5", "--slow", "15")
            run_root = root/"runs"/"sma"
            simulation = command(root, "run", "simulate", "--strategy", "sma-cross-v1@1.2.0",
                "--fixture", "--fast", "5", "--slow", "15",
                "--run-root", str(run_root))
            generic_simulation = command(root, "run", "simulate", "--strategy", "sma-cross-v1@1.2.0",
                "--fixture", "--fast", "5", "--slow", "15", "--run-root", str(root/"runs"/"sma-generic"))
            high_fee_simulation = command(root, "run", "simulate", "--strategy", "sma-cross-v1@1.2.0",
                "--fixture", "--fast", "5", "--slow", "15",
                "--fee-bps", "25", "--run-root", str(root/"runs"/"sma-high-fee"))
            calibration = command(root, "runtime", "calibrate-execution",
                "--db", high_fee_simulation["runtime_database"], "--output-root", str(root/"calibration"),
                "--venue", "simulated", "--environment", "testnet", "--strategy", "sma-cross-v1")
            calibrated_backtest = command(root, "run", "backtest", "--strategy", "sma-cross-v1@1.2.0",
                "--fixture", "--fast", "5", "--slow", "15", "--execution-calibration", calibration["manifest"])
            calibrated_artifact = json.loads(Path(calibrated_backtest["artifact"]).read_text())
            inspected = command(root, "run", "inspect", "--db", simulation["runtime_database"])
            explained = command(root, "run", "inspect", "--artifact", simulation["artifact"],
                "--at", "2026-01-02T00:00:00Z")
            replayed = command(root, "run", "artifact-replay", "--artifact", simulation["artifact"], "--fixture")
            paper=command(root,"run","paper","--strategy","sma-cross-v1@1.2.0","--fixture","--fast","5","--slow","15",
                "--run-root",str(root/"paper-runtime"),"--artifact-root",str(root/"paper-artifacts"))
            generic_paper=command(root,"run","paper","--strategy","sma-cross-v1@1.2.0","--fixture","--fast","5","--slow","15",
                "--run-root",str(root/"paper-runtime-generic"),"--artifact-root",str(root/"paper-artifacts-generic"))
            paper_replay=command(root,"run","capture-replay","--artifact",paper["artifact"],"--capture",paper["capture"])
            shadow=command(root,"run","shadow","--strategy","sma-cross-v1@1.2.0","--capture",paper["capture"],"--fast","5","--slow","15",
                "--run-root",str(root/"shadow-runtime"),"--artifact-root",str(root/"shadow-artifacts"))
            generic_shadow=command(root,"run","shadow","--strategy","sma-cross-v1@1.2.0","--capture",paper["capture"],"--fast","5","--slow","15",
                "--run-root",str(root/"shadow-runtime-generic"),"--artifact-root",str(root/"shadow-artifacts-generic"))
            shadow_replay=command(root,"run","capture-replay","--artifact",shadow["artifact"],"--capture",shadow["capture"])
            unsupported = subprocess.run(
                [sys.executable, "-m", "kairospy", "--format", "json", "--lake-root", str(root),
                 "run", "shadow", "--strategy", "covered-call-v1@1.1.0", "--fixture",
                 "--run-root", str(root/"unsupported-shadow")],
                cwd=ROOT, check=False, capture_output=True, text=True,
            )

        self.assertEqual(created["status"], "sandbox")
        self.assertEqual(frozen["status"], "frozen_candidate")
        self.assertEqual(len(factor["factor_spec_hash"]), 64)
        self.assertTrue(verified["batch_replay_equal"])
        self.assertEqual(strategy["factor_spec_hash"], factor["factor_spec_hash"])
        self.assertEqual(calibrated_backtest["execution_calibration"]["status"], "bound")
        self.assertEqual(calibrated_backtest["execution_calibration"]["release_hash"], calibration["release_hash"])
        self.assertEqual(calibrated_backtest["execution_calibration"]["sample_count"], calibration["sample_count"])
        self.assertEqual(calibrated_artifact["execution"]["calibration"]["release_hash"], calibration["release_hash"])
        self.assertEqual(calibrated_artifact["execution"]["fill_model"], "next-open-bar")
        self.assertEqual(calibrated_artifact["execution"]["calibrated_result"]["status"], "applied")
        self.assertEqual(Decimal(calibrated_artifact["execution"]["calibrated_result"]["calibrated_fee_bps"]), Decimal("25"))
        self.assertLess(Decimal(calibrated_backtest["calibrated_final_equity"]), Decimal(backtest["final_equity"]))
        for name in ("factor_hash", "decision_hash", "intent_hash"):
            self.assertEqual(backtest[name], simulation[name])
            self.assertEqual(simulation[name], generic_simulation[name])
        self.assertTrue(simulation["restart_ready"])
        self.assertTrue(generic_simulation["restart_ready"])
        self.assertGreater(inspected["transactions"], 1)
        self.assertIsNotNone(explained["factor"]); self.assertIsNotNone(explained["decision"])
        self.assertIsNotNone(explained["attribution"])
        self.assertTrue(replayed["passed"])
        self.assertEqual(paper["mode"],"paper-trading");self.assertTrue(paper["restart_ready"])
        self.assertEqual(generic_paper["mode"],"paper-trading");self.assertTrue(generic_paper["restart_ready"])
        self.assertEqual(generic_paper["factor_hash"],paper["factor_hash"])
        self.assertEqual(generic_paper["decision_hash"],paper["decision_hash"])
        self.assertEqual(generic_paper["intent_hash"],paper["intent_hash"])
        self.assertTrue(paper_replay["passed"])
        self.assertEqual(shadow["mode"],"shadow")
        self.assertEqual(generic_shadow["mode"],"shadow")
        self.assertEqual(shadow["orders"],0);self.assertEqual(shadow["fills"],0)
        self.assertEqual(shadow["submitted_orders"],0);self.assertGreater(shadow["hypothetical_intents"],0)
        self.assertEqual(shadow["factor_hash"],paper["factor_hash"])
        self.assertEqual(shadow["decision_hash"],paper["decision_hash"])
        self.assertEqual(shadow["intent_hash"],paper["intent_hash"])
        self.assertEqual(generic_shadow["factor_hash"],shadow["factor_hash"])
        self.assertEqual(generic_shadow["decision_hash"],shadow["decision_hash"])
        self.assertEqual(generic_shadow["intent_hash"],shadow["intent_hash"])
        self.assertTrue(shadow_replay["passed"])
        self.assertEqual(unsupported.returncode, 2)
        self.assertIn("currently supports sma-cross-v1", unsupported.stderr)

    def test_strategy_promotion_cli_records_gate_checked_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command(root, "strategy", "register-sma", "--input-identity", "fixture:sma-bars-v1",
                "--fast", "5", "--slow", "15")
            evidence = root / "study-result.json"
            evidence.write_text(json.dumps({"state": {"maximum_level": 2, "signal_status": "SUPPORTED"}}))
            checked = command(root, "strategy", "check-promotion", "sma-cross-v1", "--version", "1.2.0",
                "--to", "STUDY_VALIDATED", "--evidence", str(evidence))
            checked_legacy = command(root, "strategy", "check-promotion", "sma-cross-v1", "--version", "1.2.0",
                "--to", "STUDY_VALIDATED", "--evidence", str(evidence))
            before = command(root, "strategy", "status", "sma-cross-v1", "--version", "1.2.0")
            promoted = command(root, "strategy", "promote", "sma-cross-v1", "--version", "1.2.0",
                "--to", "STUDY_VALIDATED", "--evidence", str(evidence), "--actor", "reviewer",
                "--capital-limit", "10000", "--rollback-condition", "signal evidence invalidated")
            status = command(root, "strategy", "status", "sma-cross-v1", "--version", "1.2.0")
            bundle = json.loads(Path(promoted["evidence_bundle"]).read_text())

        self.assertTrue(checked["gate_passed"])
        self.assertTrue(checked["transition_valid"])
        self.assertTrue(checked["would_promote"])
        self.assertEqual(checked_legacy["target_status"], "STUDY_VALIDATED")
        self.assertEqual(before["lifecycle"], "DRAFT")
        self.assertTrue(promoted["gate_passed"])
        self.assertEqual(promoted["status"], "STUDY_VALIDATED")
        self.assertEqual(bundle["kind"], "strategy_promotion_evidence_bundle")
        self.assertEqual(bundle["to"], "STUDY_VALIDATED")
        self.assertEqual(bundle["evidence"]["evidence_paths"], [str(evidence)])
        self.assertEqual(status["latest_promotion_bundle"], promoted["evidence_bundle"])
        self.assertEqual(status["lifecycle"], "STUDY_VALIDATED")

    def test_strategy_check_promotion_reports_external_gate_failure_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command(root, "strategy", "register-sma", "--input-identity", "fixture:sma-bars-v1",
                "--fast", "5", "--slow", "15")
            fixture_l5 = root / "fixture-l5.json"
            fixture_l5.write_text(json.dumps({
                "state": {"maximum_level": 5, "strategy_status": "SUPPORTED"},
                "out_of_sample": "decision_oos",
                "evidence_scope": "local_acceptance",
            }))
            checked = command(root, "strategy", "check-promotion", "sma-cross-v1", "--version", "1.2.0",
                "--to", "PAPER_APPROVED", "--evidence", str(fixture_l5))
            status = command(root, "strategy", "status", "sma-cross-v1", "--version", "1.2.0")

        self.assertFalse(checked["gate_passed"])
        self.assertFalse(checked["would_promote"])
        self.assertIn("paper approval requires decision-OOS L5 robustness evidence", checked["gate_reasons"])
        self.assertEqual(status["lifecycle"], "DRAFT")

    def test_strategy_check_promotion_blocks_lifecycle_skip_even_when_gate_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command(root, "strategy", "register-sma", "--input-identity", "fixture:sma-bars-v1",
                "--fast", "5", "--slow", "15")
            evidence = root / "trade-proxy-result.json"
            evidence.write_text(json.dumps({"state": {"maximum_level": 3, "strategy_status": "SUPPORTED"}}))
            checked = command(root, "strategy", "check-promotion", "sma-cross-v1", "--version", "1.2.0",
                "--to", "TRADE_PROXY_VALIDATED", "--evidence", str(evidence))
            failed = subprocess.run(
                [sys.executable, "-m", "kairospy", "--format", "json", "--lake-root", str(root),
                 "strategy", "promote", "sma-cross-v1", "--version", "1.2.0",
                 "--to", "TRADE_PROXY_VALIDATED", "--evidence", str(evidence), "--actor", "reviewer",
                 "--capital-limit", "10000", "--rollback-condition", "proxy evidence invalidated"],
                cwd=ROOT, check=False, capture_output=True, text=True,
            )
            status = command(root, "strategy", "status", "sma-cross-v1", "--version", "1.2.0")

        self.assertTrue(checked["gate_passed"])
        self.assertFalse(checked["transition_valid"])
        self.assertIn("invalid strategy promotion", checked["transition_reason"])
        self.assertFalse(checked["would_promote"])
        self.assertEqual(failed.returncode, 2)
        self.assertIn("strategy promotion transition failed", failed.stderr)
        self.assertEqual(status["lifecycle"], "DRAFT")

    def test_public_binance_bar_capture_can_drive_strategy_paper(self) -> None:
        class Transport:
            def request(self, method, path, params=None, headers=None):
                self.call = (method, path, params, headers)
                start = 1_735_689_600_000
                rows = []
                for index in range(20):
                    open_time = start + index * 60_000
                    close = Decimal("100") + Decimal(index)
                    rows.append([
                        open_time, str(close - Decimal("1")), str(close + Decimal("1")),
                        str(close - Decimal("2")), str(close), "1.5", open_time + 59_999,
                    ])
                return rows

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = root / "live" / "btc.canonical.jsonl"
            written = _write_binance_spot_bar_capture(
                capture, symbol="BTCUSDT", interval="1m", limit=20,
                base_url="https://example.invalid", transport=Transport(),
            )
            paper = command(root, "run", "paper", "--strategy", "sma-cross-v1@1.2.0",
                "--capture", str(capture), "--fast", "3", "--slow", "5",
                "--run-root", str(root/"paper-runtime"), "--artifact-root", str(root/"paper-artifacts"))
            replay = command(root, "run", "capture-replay",
                "--artifact", paper["artifact"], "--capture", paper["capture"])
            artifact_exists = Path(paper["artifact"]).exists()

        self.assertEqual(written["bars"], 20)
        self.assertEqual(written["symbol"], "BTCUSDT")
        self.assertEqual(paper["mode"], "paper-trading")
        self.assertEqual(paper["bars"], 20)
        self.assertTrue(paper["input_identity"].startswith("capture:"))
        self.assertTrue(artifact_exists)
        self.assertTrue(replay["passed"])
        self.assertTrue(replay["comparisons"]["strategy_run_audit_hash"])


if __name__ == "__main__":
    unittest.main()
