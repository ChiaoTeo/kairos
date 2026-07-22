from __future__ import annotations

import json
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
            self.assertFalse((root / "studies").exists())
            self.assertFalse((root / "strategies").exists())
            self.assertTrue((root / ".kairos" / "project.json").exists())
            self.assertTrue((root / ".kairos" / "data" / "curated").is_dir())
            self.assertTrue((root / ".kairos" / "workspace").is_dir())
            self.assertTrue((root / ".kairos" / "run").is_dir())
            self.assertTrue((root / ".kairos" / "governance").is_dir())
            self.assertTrue((root / "configs" / "runs" / "backtest.example.toml").exists())
            self.assertTrue((root / "configs" / "runs" / "paper.example.toml").exists())
            self.assertTrue((root / "configs" / "runs" / "live.example.toml").exists())
            self.assertFalse((root / "data").exists())
            metadata = json.loads((root / ".kairos" / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["name"], "alpha-desk")
            self.assertEqual(metadata["root"], ".")
            config = (root / "kairos.toml").read_text(encoding="utf-8")
            self.assertIn("[credentials.massive_marketdata_primary]", config)
            self.assertIn('api_key = "env:KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY"', config)
            self.assertIn("[providers.massive]", config)
            self.assertIn("[providers.massive.services.historical_market_data]", config)
            self.assertIn("[data]", config)
            self.assertIn("[paths]", config)
            self.assertIn('lake_root = ".kairos/data"', config)
            self.assertNotIn('dataset_root = "data/curated"', config)
            self.assertIn('default_quality = "Q2"', config)
            self.assertIn("[execution]", config)
            self.assertIn("[cli]", config)
            self.assertNotIn("[study]", config)
            self.assertNotIn('default_strategy = "sma-cross-v1"', config)
            gitignore = (root / ".gitignore").read_text(encoding="utf-8")
            self.assertIn(".env", gitignore)
            self.assertNotIn("kairos.toml", gitignore)
            self.assertIn(".kairos/*", gitignore)
            self.assertIn("!.kairos/project.json", gitignore)

            readme = root / "README.md"
            readme.write_text("custom notes\n", encoding="utf-8")
            second = initialize_project(root, name="Alpha Desk")

            self.assertIn("README.md", second.reused)
            self.assertEqual(readme.read_text(encoding="utf-8"), "custom notes\n")

    def test_project_initializer_is_available_from_kairospy_namespace(self) -> None:
        from kairospy.surface.project import initialize_project as subpackage_initialize_project
        from kairospy.surface.project import initialize_project as project_initialize_project

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
            self.assertFalse((root / "studies").exists())
            self.assertTrue((root / ".kairos" / "workspace").is_dir())
            metadata = json.loads((root / ".kairos" / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["name"], "external-desk")
            self.assertEqual(metadata["root"], ".")

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
            self.assertTrue((root / "configs" / "runs" / "backtest.example.toml").exists())
            self.assertFalse((root / "config").exists())
            config = (root / "kairos.toml").read_text(encoding="utf-8")
            self.assertIn("[credentials.binance_trading_testnet_spot]", config)
            self.assertIn('api_secret = "env:KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_SECRET"', config)
            self.assertIn('lake_root = ".kairos/data"', config)
            self.assertIn("kairospy run config validate configs/runs/backtest.example.toml", "\n".join(payload["next_steps"]))

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
