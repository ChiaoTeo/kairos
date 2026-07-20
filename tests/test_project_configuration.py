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


if __name__ == "__main__":
    unittest.main()
