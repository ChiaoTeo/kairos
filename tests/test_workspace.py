from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from kairospy.data import DataSetContractArtifact, LiveViewManifest, live_view_manifest_path, write_live_view_manifest
from kairospy.data.contracts import stable_artifact_hash
from kairospy.data.products import BTC_SPOT_DAILY
from kairospy.identity import AssetId, InstrumentId, VenueId
from kairospy import Workspace, initialize_project
from kairospy.reference import ProductType, ReferenceCatalog
from kairospy.reference.contracts import CryptoSpotSpec
from kairospy.reference.repository import ReferenceCatalogRepository
from tests.reference_support import publish_test_instrument


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

    def test_run_start_paper_uses_runtime_launcher_and_governance_artifact(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Workspace Paper Run")
            Workspace.open_or_create("alpha", start=root).data.bind("bars", dataset="bars.us.equity.1d")
            (root / "paper_strategy.py").write_text(
                "\n".join([
                    "def decide(context):",
                    "    return {'action': 'hold', 'mode': context.mode, 'workspace': context.workspace}",
                ]) + "\n",
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
                    "paper",
                    "--entrypoint",
                    "paper_strategy:decide",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

            payload = json.loads(started.stdout)
            run_root = Path(payload["run_workspace"])
            self.assertEqual(payload["runtime_launch"]["status"], "running")
            self.assertEqual(payload["run_result"]["mode"], "paper-trading")
            self.assertEqual(payload["run_result"]["status"], "succeeded")
            governance_ref = payload["run_result"]["artifact_refs"][-1]
            governance = json.loads(Path(governance_ref).read_text(encoding="utf-8"))
            self.assertEqual(governance["execution"]["runtime_launch"]["runtime_id"], payload["run_id"])
            self.assertEqual(governance["execution"]["runtime_launch"]["services"]["binding_id"], "surface-run-services")
            decisions = json.loads((run_root / "artifacts" / "decisions.json").read_text(encoding="utf-8"))
            self.assertEqual(decisions[0]["mode"], "paper")

    def test_run_start_live_uses_configured_runtime_evidence_and_promotion_gate(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Workspace Live Run")
            workspace = Workspace.open_or_create("alpha", start=root)
            workspace.data.bind("bars", dataset="bars.us.equity.1d")
            (root / "live_strategy.py").write_text(
                "\n".join([
                    "def decide(context):",
                    "    return {'action': 'hold', 'mode': context.mode, 'workspace': context.workspace}",
                ]) + "\n",
                encoding="utf-8",
            )
            workspace_hash = stable_artifact_hash(workspace.snapshot())
            strategy_hash = stable_artifact_hash({
                "entrypoint": "live_strategy:decide",
                "module": "live_strategy",
                "callable": "decide",
            })
            config_hash = stable_artifact_hash({"params": {}})
            config_path = root / "kairos.toml"
            config_path.write_text(
                config_path.read_text(encoding="utf-8").replace(
                    "live_trading_enabled = false",
                    "live_trading_enabled = true",
                )
                + "\n".join([
                    "",
                    "[runtime.live]",
                    "enabled = true",
                    'profile_id = "profile:live"',
                    'provider = "binance"',
                    'execution_driver = "binance-live"',
                    'account_binding_hash = "account-binding-hash"',
                    f'data_binding_hash = "{workspace_hash}"',
                    f'strategy_hash = "{strategy_hash}"',
                    f'config_hash = "{config_hash}"',
                    'binding_id = "live-runtime-binding"',
                    "",
                    "[runtime.live.recovery]",
                    'binding_id = "live-recovery"',
                    "ready = true",
                    'reason = "startup recovery complete"',
                    "",
                    "[runtime.live.promotion]",
                    'from_stage = "PAPER_APPROVED"',
                    'to_stage = "LIVE_LIMITED"',
                    f'dataset_hash = "{workspace_hash}"',
                    f'strategy_hash = "{strategy_hash}"',
                    f'config_hash = "{config_hash}"',
                    "gate_passed = true",
                    'evidence_refs = { readiness = "readiness:live" }',
                    "",
                    "[[runtime.live.readiness]]",
                    'status = "pass"',
                    'required_ports = ["market", "reference", "execution", "account"]',
                    'account_binding = "account-binding-hash"',
                    'connector_id = "binance"',
                    'evidence_refs = { connector = "binance-live-ready" }',
                    "",
                ]),
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
                    "live",
                    "--confirm-live",
                    "--entrypoint",
                    "live_strategy:decide",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

            payload = json.loads(started.stdout)
            run_root = Path(payload["run_workspace"])
            self.assertEqual(payload["runtime_launch"]["environment"], "live")
            self.assertEqual(payload["run_result"]["mode"], "live")
            self.assertEqual(payload["run_result"]["status"], "failed")
            governance_ref = payload["run_result"]["artifact_refs"][-1]
            governance = json.loads(Path(governance_ref).read_text(encoding="utf-8"))
            prepared = governance["config"]["prepared_run"]
            self.assertEqual(prepared["evidence"]["promotion"], True)
            self.assertEqual(prepared["evidence"]["runtime_bindings"]["binding_id"], "live-runtime-binding")
            self.assertEqual(governance["execution"]["runtime_launch"]["services"]["binding_id"], "surface-live-run-services")
            decisions = json.loads((run_root / "artifacts" / "decisions.json").read_text(encoding="utf-8"))
            self.assertEqual(decisions[0]["mode"], "live")

    def test_run_start_live_provider_binding_injects_outbox_and_recovery_ports(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Workspace Live Provider Binding")
            reference_catalog_path = root / ".kairos" / "data" / "reference" / "catalog.json"
            _write_binance_reference_catalog(reference_catalog_path)
            workspace = Workspace.open_or_create("alpha", start=root)
            workspace.data.bind("bars", dataset="bars.us.equity.1d")
            (root / "live_strategy.py").write_text(
                "\n".join([
                    "def decide(context):",
                    "    return {'action': 'hold', 'mode': context.mode, 'workspace': context.workspace}",
                ]) + "\n",
                encoding="utf-8",
            )
            workspace_hash = stable_artifact_hash(workspace.snapshot())
            strategy_hash = stable_artifact_hash({
                "entrypoint": "live_strategy:decide",
                "module": "live_strategy",
                "callable": "decide",
            })
            config_hash = stable_artifact_hash({"params": {}})
            config_path = root / "kairos.toml"
            config_path.write_text(
                config_path.read_text(encoding="utf-8").replace(
                    "live_trading_enabled = false",
                    "live_trading_enabled = true",
                )
                + "\n".join([
                    "",
                    "[runtime.live]",
                    "enabled = true",
                    'profile_id = "profile:live"',
                    'provider = "binance"',
                    'execution_driver = "binance-live"',
                    'account_binding_hash = "account-binding-hash"',
                    f'data_binding_hash = "{workspace_hash}"',
                    f'strategy_hash = "{strategy_hash}"',
                    f'config_hash = "{config_hash}"',
                    'binding_id = "live-runtime-binding"',
                    "",
                    "[runtime.live.provider_binding]",
                    "enabled = true",
                    'account = "binance:crypto_spot:main"',
                    'product = "spot"',
                    'reference_catalog_path = ".kairos/data/reference/catalog.json"',
                    "",
                    "[runtime.live.recovery]",
                    'binding_id = "live-recovery"',
                    "ready = true",
                    'reason = "startup recovery complete"',
                    "",
                    "[runtime.live.promotion]",
                    'from_stage = "PAPER_APPROVED"',
                    'to_stage = "LIVE_LIMITED"',
                    f'dataset_hash = "{workspace_hash}"',
                    f'strategy_hash = "{strategy_hash}"',
                    f'config_hash = "{config_hash}"',
                    "gate_passed = true",
                    'evidence_refs = { readiness = "readiness:live" }',
                    "",
                    "[[runtime.live.readiness]]",
                    'status = "pass"',
                    'required_ports = ["market", "reference", "execution", "account"]',
                    'account_binding = "account-binding-hash"',
                    'connector_id = "binance"',
                    'evidence_refs = { connector = "binance-live-ready" }',
                    "",
                ]),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
            env["BINANCE_LIVE_API_KEY"] = "test-key"
            env["BINANCE_LIVE_API_SECRET"] = "test-secret"

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
                    "live",
                    "--confirm-live",
                    "--entrypoint",
                    "live_strategy:decide",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

            payload = json.loads(started.stdout)
            self.assertEqual(payload["runtime_launch"]["accounts"], ["binance:crypto_spot:main"])
            self.assertEqual(payload["runtime_launch"]["recovery"]["ready"], True)
            self.assertEqual(payload["runtime_launch"]["order_recovery"]["complete"], True)
            self.assertEqual(payload["run_result"]["status"], "failed")
            governance = json.loads(Path(payload["run_result"]["artifact_refs"][-1]).read_text(encoding="utf-8"))
            runtime_bindings = governance["config"]["prepared_run"]["evidence"]["runtime_bindings"]
            self.assertEqual(runtime_bindings["binding_id"], "live-runtime-binding")
            self.assertEqual(runtime_bindings["command_submitter"], "live-runtime-binding:outbox")
            self.assertEqual(runtime_bindings["recovery_handler"], "live-runtime-binding:recovery")
            self.assertEqual(runtime_bindings["market_event_provider"], "unbound")
            self.assertTrue(governance["config"]["run_request"]["metadata"]["provider_binding"])

    def test_run_start_live_market_binding_injects_data_product_event_source(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Workspace Live Market Binding")
            reference_catalog_path = root / ".kairos" / "data" / "reference" / "catalog.json"
            _write_binance_reference_catalog(reference_catalog_path)
            dataset_id = str(BTC_SPOT_DAILY.key)
            live_view_id = "live:binance:btcusdt-book"
            _write_binance_live_view(root / ".kairos" / "data", dataset_id, live_view_id)
            workspace = Workspace.open_or_create("alpha", start=root)
            workspace.data.bind_live("ticks", dataset=dataset_id)
            (root / "live_strategy.py").write_text(
                "\n".join([
                    "def decide(context):",
                    "    return {'action': 'hold', 'mode': context.mode, 'workspace': context.workspace}",
                ]) + "\n",
                encoding="utf-8",
            )
            workspace_hash = stable_artifact_hash(workspace.snapshot())
            strategy_hash = stable_artifact_hash({
                "entrypoint": "live_strategy:decide",
                "module": "live_strategy",
                "callable": "decide",
            })
            config_hash = stable_artifact_hash({"params": {}})
            config_path = root / "kairos.toml"
            config_path.write_text(
                config_path.read_text(encoding="utf-8").replace(
                    "live_trading_enabled = false",
                    "live_trading_enabled = true",
                )
                + "\n".join([
                    "",
                    "[runtime.live]",
                    "enabled = true",
                    'profile_id = "profile:live"',
                    'provider = "binance"',
                    'execution_driver = "binance-live"',
                    'account_binding_hash = "account-binding-hash"',
                    f'data_binding_hash = "{workspace_hash}"',
                    f'strategy_hash = "{strategy_hash}"',
                    f'config_hash = "{config_hash}"',
                    'binding_id = "live-runtime-binding"',
                    "",
                    "[runtime.live.provider_binding]",
                    "enabled = true",
                    'account = "binance:crypto_spot:main"',
                    'product = "spot"',
                    'reference_catalog_path = ".kairos/data/reference/catalog.json"',
                    "",
                    "[runtime.live.market_binding]",
                    "enabled = true",
                    'provider = "binance"',
                    'name = "ticks"',
                    f'dataset = "{dataset_id}"',
                    f'live_view_id = "{live_view_id}"',
                    'journal_root = ".kairos/data/live-journals"',
                    "",
                    "[runtime.live.recovery]",
                    'binding_id = "live-recovery"',
                    "ready = true",
                    'reason = "startup recovery complete"',
                    "",
                    "[runtime.live.promotion]",
                    'from_stage = "PAPER_APPROVED"',
                    'to_stage = "LIVE_LIMITED"',
                    f'dataset_hash = "{workspace_hash}"',
                    f'strategy_hash = "{strategy_hash}"',
                    f'config_hash = "{config_hash}"',
                    "gate_passed = true",
                    'evidence_refs = { readiness = "readiness:live" }',
                    "",
                    "[[runtime.live.readiness]]",
                    'status = "pass"',
                    'required_ports = ["market", "reference", "execution", "account"]',
                    'account_binding = "account-binding-hash"',
                    'connector_id = "binance"',
                    'evidence_refs = { connector = "binance-live-ready", live_view = "live:binance:btcusdt-book" }',
                    "",
                ]),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
            env["BINANCE_LIVE_API_KEY"] = "test-key"
            env["BINANCE_LIVE_API_SECRET"] = "test-secret"

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
                    "live",
                    "--confirm-live",
                    "--entrypoint",
                    "live_strategy:decide",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

            payload = json.loads(started.stdout)
            governance = json.loads(Path(payload["run_result"]["artifact_refs"][-1]).read_text(encoding="utf-8"))
            runtime_bindings = governance["config"]["prepared_run"]["evidence"]["runtime_bindings"]
            self.assertEqual(runtime_bindings["market_event_provider"], "live-runtime-binding:market-events")
            self.assertEqual(runtime_bindings["command_submitter"], "live-runtime-binding:outbox")
            self.assertTrue(governance["config"]["run_request"]["metadata"]["market_binding"])
            services = payload["runtime_launch"]["services"]["services"]
            self.assertEqual(
                sorted(item["name"] for item in services),
                [
                    "feed-monitor:ticks:live:binance:btcusdt-book",
                    "feed:ticks:live:binance:btcusdt-book",
                ],
            )
            self.assertEqual({item["status"] for item in services}, {"created"})

    def test_run_start_live_market_binding_can_supervise_data_product_services(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Workspace Live Supervised Market Binding")
            reference_catalog_path = root / ".kairos" / "data" / "reference" / "catalog.json"
            _write_binance_reference_catalog(reference_catalog_path)
            dataset_id = str(BTC_SPOT_DAILY.key)
            live_view_id = "live:binance:btcusdt-book"
            _write_binance_live_view(root / ".kairos" / "data", dataset_id, live_view_id)
            workspace = Workspace.open_or_create("alpha", start=root)
            workspace.data.bind_live("ticks", dataset=dataset_id)
            workspace_hash = stable_artifact_hash(workspace.snapshot())
            strategy_hash = stable_artifact_hash({
                "entrypoint": "live_strategy:decide",
                "module": "live_strategy",
                "callable": "decide",
            })
            config_hash = stable_artifact_hash({"params": {}})
            config_path = root / "kairos.toml"
            config_path.write_text(
                config_path.read_text(encoding="utf-8").replace(
                    "live_trading_enabled = false",
                    "live_trading_enabled = true",
                )
                + "\n".join([
                    "",
                    "[runtime.live]",
                    "enabled = true",
                    'profile_id = "profile:live"',
                    'provider = "binance"',
                    'execution_driver = "binance-live"',
                    'account_binding_hash = "account-binding-hash"',
                    f'data_binding_hash = "{workspace_hash}"',
                    f'strategy_hash = "{strategy_hash}"',
                    f'config_hash = "{config_hash}"',
                    'binding_id = "live-runtime-binding"',
                    "",
                    "[runtime.live.provider_binding]",
                    "enabled = true",
                    'account = "binance:crypto_spot:main"',
                    'product = "spot"',
                    'reference_catalog_path = ".kairos/data/reference/catalog.json"',
                    "",
                    "[runtime.live.market_binding]",
                    "enabled = true",
                    "supervise_services = true",
                    'provider = "binance"',
                    'name = "ticks"',
                    f'dataset = "{dataset_id}"',
                    f'live_view_id = "{live_view_id}"',
                    "",
                    "[runtime.live.recovery]",
                    'binding_id = "live-recovery"',
                    "ready = true",
                    'reason = "startup recovery complete"',
                    "",
                    "[runtime.live.promotion]",
                    'from_stage = "PAPER_APPROVED"',
                    'to_stage = "LIVE_LIMITED"',
                    f'dataset_hash = "{workspace_hash}"',
                    f'strategy_hash = "{strategy_hash}"',
                    f'config_hash = "{config_hash}"',
                    "gate_passed = true",
                    'evidence_refs = { readiness = "readiness:live" }',
                    "",
                    "[[runtime.live.readiness]]",
                    'status = "pass"',
                    'required_ports = ["market", "reference", "execution", "account"]',
                    'account_binding = "account-binding-hash"',
                    'connector_id = "binance"',
                    'evidence_refs = { connector = "binance-live-ready", live_view = "live:binance:btcusdt-book" }',
                    "",
                ]),
                encoding="utf-8",
            )

            from kairospy.infrastructure.configuration import KairosProjectConfig
            from kairospy.integrations import LiveMarketEventSourceBinding, LiveProviderPorts
            from kairospy.integrations.connectors.simulated import SimulatedExecutionAccountGateway
            from kairospy.integrations.ports import Environment
            from kairospy.identity import AccountRef, AccountType, InstitutionId
            from kairospy.market.stream import IterableEventSource
            from kairospy.runtime import ManagedServiceSpec
            from kairospy.surface.product import _launch_workspace_live_run, _strategy_run_result_from_workspace_execution

            account = AccountRef(InstitutionId("binance"), "main", AccountType.CRYPTO_SPOT)
            gateway = SimulatedExecutionAccountGateway(VenueId("binance"), account, environment=Environment.LIVE)
            lifecycle: list[str] = []

            async def fake_feed() -> None:
                lifecycle.append("started")
                try:
                    await asyncio.Future()
                finally:
                    lifecycle.append("stopped")

            market_source = LiveMarketEventSourceBinding(
                "binance",
                "ticks",
                dataset_id,
                live_view_id,
                IterableEventSource(()),
                "feed:ticks",
                (ManagedServiceSpec("feed:ticks", fake_feed),),
                "plan-hash",
                "bundle-hash",
                live_view_manifest_path(root / ".kairos" / "data", dataset_id, live_view_id),
            )
            provider_ports = LiveProviderPorts(
                "binance",
                "binance-live",
                account,
                gateway,
                gateway,
                None,
            )

            with (
                patch("kairospy.integrations.live_ports.build_live_provider_ports", return_value=provider_ports),
                patch("kairospy.integrations.live_ports.build_live_market_event_source", return_value=market_source),
            ):
                runtime_launch, run_result = _launch_workspace_live_run(
                    KairosProjectConfig.discover(root),
                    root / ".kairos" / "run" / "supervised",
                    "run_supervised",
                    workspace.name,
                    workspace_hash,
                    strategy_hash,
                    config_hash,
                    {},
                    lambda _prepared: _strategy_run_result_from_workspace_execution({"decisions": []}),
                    confirm_live=True,
                )

            self.assertEqual(lifecycle, ["started", "stopped"])
            self.assertEqual(runtime_launch["services"]["binding_id"], "surface-live-run-services")
            self.assertEqual(runtime_launch["services"]["services"][0]["status"], "stopped")
            governance = json.loads(Path(run_result.artifact_refs[-1]).read_text(encoding="utf-8"))
            self.assertTrue(governance["config"]["run_request"]["metadata"]["market_services_supervised"])
            self.assertEqual(
                governance["execution"]["runtime_launch"]["services"]["services"][0]["status"],
                "stopped",
            )

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


def _write_binance_reference_catalog(path: Path) -> None:
    at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    catalog = ReferenceCatalog()
    publish_test_instrument(
        catalog,
        InstrumentId("BTC-USDT"),
        ProductType.CRYPTO_SPOT,
        "BTC/USDT",
        CryptoSpotSpec(AssetId("BTC"), AssetId("USDT"), Decimal("10")),
        AssetId("USDT"),
        VenueId("binance"),
        "BTCUSDT",
        at,
        quantity_increment=Decimal("0.001"),
        minimum_quantity=Decimal("0.001"),
    )
    ReferenceCatalogRepository(path).save(catalog)


def _write_binance_live_view(root: Path, dataset_id: str, live_view_id: str) -> Path:
    manifest = LiveViewManifest(
        dataset_id,
        live_view_id,
        DataSetContractArtifact.from_product_contract(BTC_SPOT_DAILY).contract_hash,
        "connector-hash",
        "available_time",
        ("available_time", "bid", "ask"),
        {
            "provider": "binance",
            "event_source_contract": "EventSource[DataSetRecord]",
            "channel_contract": "BoundedEventChannel",
            "freshness": {"max_age_seconds": 60},
            "channel_capacity": 8,
        },
        {
            "kind": "binance_market_stream",
            "provider": "binance",
            "symbol": "BTCUSDT",
            "channel": "bookTicker",
            "instrument_id": "crypto:binance:spot:BTCUSDT",
            "public_only": True,
        },
        "configured",
        "2026-07-20T00:00:00+00:00",
    )
    path = live_view_manifest_path(root, dataset_id, live_view_id)
    write_live_view_manifest(path, manifest)
    return path


if __name__ == "__main__":
    unittest.main()
