from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from kairos import initialize_project


class KairosProjectInitTests(unittest.TestCase):
    def test_initialize_project_creates_safe_repeatable_project_scaffold(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result = initialize_project(root, name="Alpha Desk")

            self.assertEqual(result.name, "alpha-desk")
            self.assertTrue((root / "kairos.toml").exists())
            self.assertTrue((root / "pyproject.toml").exists())
            self.assertTrue((root / "research" / "starter.py").exists())
            self.assertTrue((root / "strategies" / "starter_sma.py").exists())
            self.assertTrue((root / ".kairos" / "project.json").exists())

            readme = root / "README.md"
            readme.write_text("custom notes\n", encoding="utf-8")
            second = initialize_project(root, name="Alpha Desk")

            self.assertIn("README.md", second.reused)
            self.assertEqual(readme.read_text(encoding="utf-8"), "custom notes\n")

    def test_project_initializer_is_available_from_kairos_namespace(self) -> None:
        from kairos.project import initialize_project as subpackage_initialize_project
        from kairos.project import initialize_project as trading_initialize_project

        self.assertIs(initialize_project, trading_initialize_project)
        self.assertIs(subpackage_initialize_project, trading_initialize_project)

    def test_kairos_init_cli_bootstraps_a_runnable_external_project(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "external-project"
            completed = subprocess.run(
                [sys.executable, "-m", "kairos", "--format", "json", "init", "--target", str(root), "--name", "External Desk"],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)

            self.assertEqual(payload["name"], "external-desk")
            self.assertTrue((root / "research" / "starter.py").exists())

            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
            starter = subprocess.run(
                [sys.executable, "research/starter.py"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn("final_equity", starter.stdout)


if __name__ == "__main__":
    unittest.main()
