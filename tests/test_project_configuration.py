from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from kairos.configuration import KairosProjectConfig, set_config_value, unset_config_value
from kairos.project import initialize_project


class KairosProjectConfigurationTests(unittest.TestCase):
    def test_project_config_discovers_resolves_and_redacts_provider_values(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Config Desk")

            with self.assertRaisesRegex(Exception, "Massive API key is missing"):
                KairosProjectConfig.discover(root).massive_config()

            env = {"MASSIVE_API_KEY": "secret"}
            old = os.environ.copy()
            os.environ.update(env)
            try:
                config = KairosProjectConfig.discover(root / "studies")
                self.assertEqual(config.root, root.resolve())
                self.assertEqual(config.massive_config().api_key, "secret")
                self.assertEqual(config.to_redacted_dict()["providers"]["massive"]["api_key"], "env:MASSIVE_API_KEY")
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_set_and_unset_config_value_preserves_valid_toml(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Config Desk")
            path = root / "kairos.toml"

            set_config_value(path, "providers.massive.api_key", "env:KAIROS_MASSIVE_KEY")
            config = KairosProjectConfig.load(path)
            self.assertEqual(config.get("providers.massive.api_key"), "env:KAIROS_MASSIVE_KEY")

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
                [sys.executable, "-m", "kairos", "--format", "json", "config", "show"],
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
                [sys.executable, "-m", "kairos", "--format", "json", "doctor"],
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
                [sys.executable, "-m", "kairos", "configure", "massive", "--api-key-env", "MY_MASSIVE_KEY"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            subprocess.run(
                [
                    sys.executable, "-m", "kairos", "configure", "binance",
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
                [sys.executable, "-m", "kairos", "doctor"],
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
                [sys.executable, "-m", "kairos", "project", "status"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn("Kairos Project Status", status.stdout)
            self.assertIn("output-desk", status.stdout)

            show = subprocess.run(
                [sys.executable, "-m", "kairos", "config", "show"],
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
                [sys.executable, "-m", "kairos", "configure", "--interactive"],
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

    def test_cli_init_interactive_accepts_piped_answers(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "desk"
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")

            subprocess.run(
                [sys.executable, "-m", "kairos", "init", "--interactive"],
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
                    sys.executable, "-m", "kairos", "run", "backtest",
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
                    sys.executable, "-m", "kairos", "--format", "json", "run", "backtest",
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


if __name__ == "__main__":
    unittest.main()
