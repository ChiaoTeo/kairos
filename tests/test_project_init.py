from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from kairospy import initialize_project


class KairosProjectInitTests(unittest.TestCase):
    def test_initialize_project_creates_safe_repeatable_project_scaffold(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result = initialize_project(root, name="Alpha Desk")

            self.assertEqual(result.name, "alpha-desk")
            self.assertTrue((root / "kairos.toml").exists())
            self.assertTrue((root / ".env.example").exists())
            self.assertTrue((root / "pyproject.toml").exists())
            self.assertFalse((root / "config").exists())
            self.assertFalse((root / "config" / "research.json").exists())
            self.assertTrue((root / "studies" / "starter.py").exists())
            self.assertTrue((root / "strategies" / "starter_sma.py").exists())
            self.assertTrue((root / ".kairos" / "project.json").exists())
            metadata = json.loads((root / ".kairos" / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["name"], "alpha-desk")
            self.assertEqual(metadata["root"], ".")
            config = (root / "kairos.toml").read_text(encoding="utf-8")
            self.assertIn("[providers.massive]", config)
            self.assertIn('api_key = "env:MASSIVE_API_KEY"', config)
            self.assertIn("[data]", config)
            self.assertIn('default_quality = "Q2"', config)
            self.assertIn("[execution]", config)
            self.assertIn("[cli]", config)
            self.assertIn('default_dataset = "fixture:sma-bars-v1"', config)
            self.assertIn('default_strategy = "sma-cross-v1"', config)
            self.assertIn(".env", (root / ".gitignore").read_text(encoding="utf-8"))

            readme = root / "README.md"
            readme.write_text("custom notes\n", encoding="utf-8")
            second = initialize_project(root, name="Alpha Desk")

            self.assertIn("README.md", second.reused)
            self.assertEqual(readme.read_text(encoding="utf-8"), "custom notes\n")

    def test_project_initializer_is_available_from_kairospy_namespace(self) -> None:
        from kairospy.project import initialize_project as subpackage_initialize_project
        from kairospy.project import initialize_project as project_initialize_project

        self.assertIs(initialize_project, project_initialize_project)
        self.assertIs(subpackage_initialize_project, project_initialize_project)

    def test_kairospy_init_cli_bootstraps_a_runnable_external_project(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "external-project"
            completed = subprocess.run(
                [sys.executable, "-m", "kairospy", "--format", "json", "init", "--target", str(root), "--name", "External Desk"],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)

            self.assertEqual(payload["name"], "external-desk")
            self.assertTrue((root / "studies" / "starter.py").exists())
            metadata = json.loads((root / ".kairos" / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["name"], "external-desk")
            self.assertEqual(metadata["root"], ".")

            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
            starter = subprocess.run(
                [sys.executable, "studies/starter.py"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn("final_equity", starter.stdout)

    def test_kairospy_init_accepts_positional_project_directory_and_writes_config(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "positional-project"
            completed = subprocess.run(
                [sys.executable, "-m", "kairospy", "--format", "json", "init", str(root), "--name", "Positional Desk"],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)

            self.assertEqual(payload["name"], "positional-desk")
            self.assertTrue((root / "kairos.toml").exists())
            self.assertTrue((root / ".env.example").exists())
            self.assertFalse((root / "config").exists())
            config = (root / "kairos.toml").read_text(encoding="utf-8")
            self.assertIn("[providers.binance.testnet]", config)
            self.assertIn('api_secret = "env:BINANCE_TESTNET_API_SECRET"', config)
            self.assertIn("kairospy configure massive", "\n".join(payload["next_steps"]))

    def test_source_repository_default_name_remains_kairospy_even_when_directory_is_trader(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "trader"
            (root / "kairospy").mkdir(parents=True)
            (root / "pyproject.toml").write_text('[project]\nname = "kairospy"\n', encoding="utf-8")

            result = initialize_project(root)
            metadata = json.loads((root / ".kairos" / "project.json").read_text(encoding="utf-8"))

            self.assertEqual(result.name, "kairospy")
            self.assertEqual(metadata["name"], "kairospy")
            self.assertEqual(metadata["root"], ".")


if __name__ == "__main__":
    unittest.main()
