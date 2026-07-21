from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from kairospy import Workspace, initialize_project


class WorkspaceTests(unittest.TestCase):
    def test_workspace_open_or_create_writes_project_workspace_manifest(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Workspace Desk")

            workspace = Workspace.open_or_create("alpha", start=root)
            binding = workspace.data.bind("bars", dataset="bars.us.equity.1d")

            self.assertEqual(binding.name, "bars")
            self.assertEqual(binding.dataset, "bars.us.equity.1d")
            self.assertTrue((root / ".kairos" / "workspace" / "alpha" / "workspace.json").exists())
            aliases = json.loads(
                (root / ".kairos" / "workspace" / "alpha" / "data" / "aliases.json").read_text(encoding="utf-8")
            )
            self.assertEqual(aliases["bindings"]["bars"]["dataset"], "bars.us.equity.1d")
            self.assertFalse((root / ".kairos" / "data" / "workspaces").exists())

    def test_workspace_cli_creates_and_binds_data(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Workspace Cli")
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")

            create = subprocess.run(
                [sys.executable, "-m", "kairospy", "--format", "json", "workspace", "create", "alpha"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(json.loads(create.stdout)["workspace"], "alpha")

            bind = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kairospy",
                    "--format",
                    "json",
                    "workspace",
                    "bind-data",
                    "alpha",
                    "--name",
                    "bars",
                    "--dataset",
                    "bars.us.equity.1d",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            payload = json.loads(bind.stdout)
            self.assertEqual(payload["binding"]["name"], "bars")
            self.assertEqual(payload["binding"]["dataset"], "bars.us.equity.1d")

    def test_run_start_uses_workspace_and_strategy_entrypoint(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Workspace Run")
            Workspace.open_or_create("alpha", start=root).data.bind("bars", dataset="bars.us.equity.1d")
            (root / "my_strategy.py").write_text(
                "\n".join(
                    [
                        "REQUIRES = {'inputs': {'bars': {'kind': 'dataset'}}}",
                        "",
                        "def decide(context):",
                        "    return {'action': 'hold', 'workspace': context.workspace, 'fast': context.params['fast']}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")

            started = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kairospy",
                    "--format",
                    "json",
                    "run",
                    "start",
                    "--workspace",
                    "alpha",
                    "--mode",
                    "backtest",
                    "--entrypoint",
                    "my_strategy:decide",
                    "--param",
                    "fast=20",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

            payload = json.loads(started.stdout)
            run_root = Path(payload["run_workspace"])
            self.assertEqual(payload["workspace"]["name"], "alpha")
            self.assertEqual(payload["strategy"]["entrypoint"], "my_strategy:decide")
            self.assertEqual(payload["strategy"]["params"]["fast"], "20")
            self.assertEqual(run_root.parent.resolve(), (root / ".kairos" / "run").resolve())
            self.assertTrue((run_root / "manifest.json").exists())
            self.assertTrue((run_root / "workspace_snapshot.json").exists())
            decisions = json.loads((run_root / "artifacts" / "decisions.json").read_text(encoding="utf-8"))
            self.assertEqual(decisions[0]["workspace"], "alpha")

            inspected = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kairospy",
                    "--format",
                    "json",
                    "run",
                    "inspect",
                    "--run-id",
                    payload["run_id"],
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(json.loads(inspected.stdout)["run_id"], payload["run_id"])

            replayed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kairospy",
                    "--format",
                    "json",
                    "run",
                    "replay",
                    "--run-id",
                    payload["run_id"],
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertTrue(json.loads(replayed.stdout)["passed"])

    def test_run_start_accepts_workspace_function_artifact(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Workspace Function Run")
            Workspace.open_or_create("alpha", start=root).data.bind("bars", dataset="bars.us.equity.1d")
            (root / "research_report.py").write_text(
                "\n".join(
                    [
                        "REQUIRES = {'inputs': {'bars': {'kind': 'dataset'}}}",
                        "",
                        "def run(workspace, params):",
                        "    binding = workspace.binding('bars')",
                        "    return {'artifact': 'momentum_report', 'dataset': binding.dataset, 'window': params['window']}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")

            started = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kairospy",
                    "--format",
                    "json",
                    "run",
                    "start",
                    "--workspace",
                    "alpha",
                    "--mode",
                    "backtest",
                    "--entrypoint",
                    "research_report:run",
                    "--param",
                    "window=30d",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

            payload = json.loads(started.stdout)
            run_root = Path(payload["run_workspace"])
            self.assertEqual(payload["strategy"]["entrypoint_kind"], "workspace_function")
            self.assertEqual(payload["artifacts"]["result"], str(run_root / "artifacts" / "result.json"))
            self.assertEqual(json.loads((run_root / "artifacts" / "decisions.json").read_text(encoding="utf-8")), [])
            result = json.loads((run_root / "artifacts" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["artifact"], "momentum_report")
            self.assertEqual(result["dataset"], "bars.us.equity.1d")
            self.assertEqual(result["window"], "30d")

    def test_run_start_accepts_workspace_strategy_builder(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Workspace Strategy Builder")
            Workspace.open_or_create("alpha", start=root).data.bind("bars", dataset="bars.us.equity.1d")
            (root / "builder_strategy.py").write_text(
                "\n".join(
                    [
                        "class Strategy:",
                        "    def __init__(self, workspace, params):",
                        "        self.workspace = workspace",
                        "        self.params = params",
                        "",
                        "    def decide(self, context):",
                        "        return {'action': 'hold', 'workspace': context.workspace, 'slow': self.params['slow']}",
                        "",
                        "def build(workspace, params):",
                        "    return Strategy(workspace, params)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")

            started = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kairospy",
                    "--format",
                    "json",
                    "run",
                    "start",
                    "--workspace",
                    "alpha",
                    "--mode",
                    "historical-simulation",
                    "--entrypoint",
                    "builder_strategy:build",
                    "--param",
                    "slow=50",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

            payload = json.loads(started.stdout)
            run_root = Path(payload["run_workspace"])
            self.assertEqual(payload["strategy"]["entrypoint_kind"], "workspace_strategy")
            self.assertNotIn("result", payload["artifacts"])
            decisions = json.loads((run_root / "artifacts" / "decisions.json").read_text(encoding="utf-8"))
            self.assertEqual(decisions[0]["workspace"], "alpha")
            self.assertEqual(decisions[0]["slow"], "50")

    def test_run_start_rejects_legacy_snapshot_target(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Workspace Run Legacy")
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")

            rejected = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kairospy",
                    "--format",
                    "json",
                    "run",
                    "start",
                    "--workspace",
                    "alpha",
                    "--entrypoint",
                    "my_strategy:decide",
                    "--snapshot",
                    "old@1.0.0",
                    "--mode",
                    "backtest",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("unrecognized arguments: --snapshot", rejected.stderr)

    def test_cli_does_not_expose_study_or_strategy_workspaces(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "kairospy", "--help"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": os.getcwd() + os.pathsep + os.environ.get("PYTHONPATH", "")},
        )

        self.assertNotIn("\n    study", completed.stdout)
        self.assertNotIn("\n    strategy", completed.stdout)
        self.assertIn("workspace", completed.stdout)

    def test_data_for_uses_workspace_not_study(self) -> None:
        env = {**os.environ, "PYTHONPATH": os.getcwd() + os.pathsep + os.environ.get("PYTHONPATH", "")}
        for command in (
            [sys.executable, "-m", "kairospy", "data", "start", "--help"],
            [sys.executable, "-m", "kairospy", "data", "use", "--help"],
            [sys.executable, "-m", "kairospy", "data", "promote", "--help"],
        ):
            completed = subprocess.run(command, check=True, capture_output=True, text=True, env=env)
            self.assertIn("workspace", completed.stdout)
            self.assertNotIn("{study", completed.stdout)
            self.assertNotIn("approved_for_workspace", completed.stdout)

    def test_run_help_only_exposes_workspace_lifecycle(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "kairospy", "run", "--help"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": os.getcwd() + os.pathsep + os.environ.get("PYTHONPATH", "")},
        )

        self.assertIn("start", completed.stdout)
        self.assertIn("inspect", completed.stdout)
        self.assertIn("replay", completed.stdout)
        self.assertIn("compare", completed.stdout)
        for old in ("backtest", "simulate", "paper", "shadow", "artifact-replay", "capture-replay"):
            self.assertNotIn(f"\n    {old}", completed.stdout)


if __name__ == "__main__":
    unittest.main()
