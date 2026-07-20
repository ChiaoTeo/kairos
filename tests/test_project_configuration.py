from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from kairospy.configuration import KairosProjectConfig, set_config_value, unset_config_value
from kairospy.project import initialize_project


class KairosProjectConfigurationTests(unittest.TestCase):
    def test_project_config_discovers_resolves_and_redacts_provider_values(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Config Desk")

            env = {"MASSIVE_API_KEY": "secret"}
            old = os.environ.copy()
            try:
                os.environ.pop("MASSIVE_API_KEY", None)
                with self.assertRaisesRegex(Exception, "Massive API key is missing"):
                    KairosProjectConfig.discover(root).massive_config()
                os.environ.update(env)
                config = KairosProjectConfig.discover(root / "studies")
                self.assertEqual(config.root, root.resolve())
                self.assertEqual(config.massive_config().api_key, "secret")
                self.assertEqual(config.to_redacted_dict()["providers"]["massive"]["api_key"], "env:MASSIVE_API_KEY")
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_project_config_loads_dotenv_without_overriding_existing_environment(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Dotenv Desk")
            (root / ".env").write_text("MASSIVE_API_KEY=dotenv-secret\nQUOTED=\"quoted value\"\n", encoding="utf-8")
            old = os.environ.copy()
            os.environ.pop("MASSIVE_API_KEY", None)
            os.environ.pop("QUOTED", None)
            try:
                self.assertEqual(KairosProjectConfig.discover(root).massive_config().api_key, "dotenv-secret")
                self.assertEqual(os.environ["QUOTED"], "quoted value")
                os.environ["MASSIVE_API_KEY"] = "shell-secret"
                self.assertEqual(KairosProjectConfig.discover(root).massive_config().api_key, "shell-secret")
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_data_bootstrap_import_is_safe_from_cold_process(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [
                sys.executable, "-c",
                "from kairospy.data.bootstrap import register_default_products; "
                "from kairospy.connectors.binance import BinanceRuntimeFeedFactory; "
                "from kairospy.study_platform.session import open_study; "
                "print(register_default_products.__name__, BinanceRuntimeFeedFactory.__name__, open_study.__name__)",
            ],
            cwd=os.getcwd(),
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertIn("register_default_products BinanceRuntimeFeedFactory open_study", result.stdout)

    def test_set_and_unset_config_value_preserves_valid_toml(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Config Desk")
            path = root / "kairos.toml"

            set_config_value(path, "providers.massive.api_key", "env:KAIROSPY_MASSIVE_KEY")
            config = KairosProjectConfig.load(path)
            self.assertEqual(config.get("providers.massive.api_key"), "env:KAIROSPY_MASSIVE_KEY")

            self.assertTrue(unset_config_value(path, "providers.massive.api_key"))
            config = KairosProjectConfig.load(path)
            self.assertIsNone(config.get("providers.massive.api_key"))

    def test_cli_config_and_doctor_use_local_project(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Cli Desk")
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
            env["MASSIVE_API_KEY"] = "secret"

            show = subprocess.run(
                [sys.executable, "-m", "kairospy", "--format", "json", "config", "show"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            payload = json.loads(show.stdout)
            self.assertEqual(payload["project"]["name"], "cli-desk")
            self.assertEqual(payload["providers"]["massive"]["api_key"], "env:MASSIVE_API_KEY")

            doctor = subprocess.run(
                [sys.executable, "-m", "kairospy", "--format", "json", "doctor"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            checks = json.loads(doctor.stdout)["checks"]
            self.assertIn({"name": "massive", "status": "ok", "detail": "credentials resolved"}, checks)
            self.assertIn("next_steps", json.loads(doctor.stdout))

    def test_cli_configure_provider_shortcuts_write_project_config(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Provider Desk")
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")

            subprocess.run(
                [sys.executable, "-m", "kairospy", "configure", "massive", "--api-key-env", "MY_MASSIVE_KEY"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            subprocess.run(
                [
                    sys.executable, "-m", "kairospy", "configure", "binance",
                    "--environment", "live", "--api-key-env", "MY_BINANCE_KEY", "--api-secret-env", "MY_BINANCE_SECRET",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

            config = KairosProjectConfig.load(root / "kairos.toml")
            self.assertEqual(config.get("providers.massive.api_key"), "env:MY_MASSIVE_KEY")
            self.assertEqual(config.get("providers.binance.live.api_key"), "env:MY_BINANCE_KEY")
            self.assertEqual(config.get("providers.binance.live.api_secret"), "env:MY_BINANCE_SECRET")

    def test_cli_human_output_uses_professional_status_tables(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Output Desk")
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
            env["MASSIVE_API_KEY"] = "secret"

            doctor = subprocess.run(
                [sys.executable, "-m", "kairospy", "doctor"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn("Kairos Doctor", doctor.stdout)
            self.assertIn("massive", doctor.stdout)
            self.assertIn("OK", doctor.stdout)

            status = subprocess.run(
                [sys.executable, "-m", "kairospy", "project", "status"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn("Kairos Project Status", status.stdout)
            self.assertIn("output-desk", status.stdout)
            self.assertIn(".kairos/data", status.stdout)

            show = subprocess.run(
                [sys.executable, "-m", "kairospy", "config", "show"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn("Kairos Configuration", show.stdout)
            self.assertIn("providers.massive.api_key", show.stdout)

    def test_cli_configure_interactive_accepts_piped_answers(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Interactive Desk")
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")

            subprocess.run(
                [sys.executable, "-m", "kairospy", "configure", "--interactive"],
                cwd=root,
                input="binance\ntestnet\nPIPE_BINANCE_KEY\nPIPE_BINANCE_SECRET\n",
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

            config = KairosProjectConfig.load(root / "kairos.toml")
            self.assertEqual(config.get("providers.binance.testnet.api_key"), "env:PIPE_BINANCE_KEY")
            self.assertEqual(config.get("providers.binance.testnet.api_secret"), "env:PIPE_BINANCE_SECRET")

    def test_cli_data_acquire_lists_products_and_accepts_interactive_dry_run(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Acquire Desk")
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")

            listed = subprocess.run(
                [sys.executable, "-m", "kairospy", "--format", "json", "data", "acquire", "--list-products"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn("market.ohlcv.crypto.binance.usdm-perpetual.1h", listed.stdout)

            dry_run = subprocess.run(
                [sys.executable, "-m", "kairospy", "data", "acquire", "--dry-run"],
                cwd=root,
                input=(
                    "market.ohlcv.crypto.binance.btc-usdt.1d\n"
                    "2026-01-01T00:00:00+00:00\n"
                    "2026-01-02T00:00:00+00:00\n"
                    "full-market\n"
                ),
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn("Acquirable Data Products", dry_run.stdout)
            self.assertIn("Kairos Acquisition Plan", dry_run.stdout)
            self.assertIn("market.ohlcv.crypto.binance.btc-usdt.1d", dry_run.stdout)

            (root / ".env").write_text("MASSIVE_API_KEY=test-key\n", encoding="utf-8")
            massive_human_plan = subprocess.run(
                [
                    sys.executable, "-m", "kairospy", "data", "acquire",
                    "--dataset", "market.ohlcv.equity.us.massive.1h.adjusted",
                    "--start", "2026-01-02T14:30:00+00:00",
                    "--end", "2026-01-02T16:30:00+00:00",
                    "--provider", "massive", "--venue", "us-securities",
                    "--instrument", "equity:us:AAPL", "--dry-run",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn("Provider Task Plan", massive_human_plan.stdout)
            self.assertIn("rest-paginated-aggregate", massive_human_plan.stdout)
            massive_plan = subprocess.run(
                [
                    sys.executable, "-m", "kairospy", "--format", "json", "data", "acquire",
                    "--dataset", "market.ohlcv.equity.us.massive.1h.adjusted",
                    "--start", "2026-01-02T14:30:00+00:00",
                    "--end", "2026-01-02T16:30:00+00:00",
                    "--provider", "massive", "--venue", "us-securities",
                    "--instrument", "equity:us:AAPL", "--dry-run",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            payload = json.loads(massive_plan.stdout)
            self.assertEqual(payload["estimate"]["requests"], 1)
            self.assertEqual(payload["provider_tasks"]["task_type"], "rest-paginated-aggregate")
            self.assertEqual(payload["provider_tasks"]["total_tasks"], 1)
            self.assertEqual(payload["provider_tasks"]["uncached_tasks"], 1)

            blocked = subprocess.run(
                [
                    sys.executable, "-m", "kairospy", "data", "acquire",
                    "--dataset", "market.ohlcv.equity.us.massive.1h.adjusted",
                    "--start", "2026-01-02T14:30:00+00:00",
                    "--end", "2026-01-02T16:30:00+00:00",
                    "--provider", "massive", "--venue", "us-securities",
                    "--max-requests", "1", "--yes",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("acquisition estimates", blocked.stderr + blocked.stdout)

    def test_cli_init_interactive_accepts_piped_answers(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "desk"
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")

            subprocess.run(
                [sys.executable, "-m", "kairospy", "init", "--interactive"],
                cwd=root,
                input=f"{target}\nInteractive Desk\nno\n",
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

            config = KairosProjectConfig.load(target / "kairos.toml")
            self.assertEqual(config.get("project.name"), "interactive-desk")

    def test_run_control_console_and_json_output_are_separate(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")

            human = subprocess.run(
                [
                    sys.executable, "-m", "kairospy", "run", "backtest",
                    "--fixture", "--fast", "5", "--slow", "15", "--artifact-root", str(root / "artifacts"),
                    "--control",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn("Kairos Run Control", human.stdout)
            self.assertIn("Kairos Run Summary", human.stdout)
            self.assertIn("Next Steps", human.stdout)

            machine = subprocess.run(
                [
                    sys.executable, "-m", "kairospy", "--format", "json", "run", "backtest",
                    "--fixture", "--fast", "5", "--slow", "15", "--artifact-root", str(root / "json-artifacts"),
                    "--control",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            payload = json.loads(machine.stdout)
            self.assertEqual(payload["mode"], "backtest")
            self.assertNotIn("Kairos Run Control", machine.stdout)

    def test_data_catalog_human_output_and_json_output_are_separate(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")

            human = subprocess.run(
                [sys.executable, "-m", "kairospy", "--lake-root", str(root / "lake"), "data", "catalog", "--refresh"],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn("Kairos Data Catalog", human.stdout)
            self.assertIn("Binance BTC/USDT daily OHLCV", human.stdout)
            self.assertIn("Primary Time", human.stdout)

            machine = subprocess.run(
                [
                    sys.executable, "-m", "kairospy", "--format", "json", "--lake-root", str(root / "json-lake"),
                    "data", "catalog", "--refresh",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            payload = json.loads(machine.stdout)
            self.assertIn("products", payload)
            self.assertNotIn("Kairos Data Catalog", machine.stdout)


if __name__ == "__main__":
    unittest.main()
