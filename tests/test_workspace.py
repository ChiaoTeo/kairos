from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kairospy.data.contracts import DataSetContractArtifact, LiveViewManifest, stable_artifact_hash
from kairospy.data.quality.freshness import live_view_manifest_path, write_live_view_manifest
from kairospy.data.products import BTC_SPOT_DAILY
from kairospy.identity import AssetId, InstrumentId, VenueId
from kairospy import Workspace, initialize_project
from kairospy.reference import ProductType, ReferenceCatalog
from kairospy.reference.contracts import CryptoSpotSpec
from kairospy.reference.repository import ReferenceCatalogRepository
from tests.reference_support import publish_test_instrument


def _write_run_config(
    root: Path,
    filename: str,
    *,
    mode: str,
    strategy: str,
    params: dict[str, str] | None = None,
    bind_provider: bool = False,
    market: str = "bars",
    extra: str = "",
) -> Path:
    _write_default_workspace_code(root)
    path = root / "configs" / "runs" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    merged_params = {"workspace_profile": "alpha", **(params or {})}
    param_lines = "\n".join(f'{key} = "{value}"' for key, value in merged_params.items())
    sections = [
        "schema_version = 1",
        "",
        "[run]",
        f'name = "{Path(filename).stem}"',
        f'mode = "{mode}"',
        'workspace = "workspace_code:build_workspace"',
        f'strategy = "{strategy}"',
    ]
    if param_lines:
        sections.extend(["", "[params]", param_lines])
    if mode == "paper":
        sections.extend(["", "[bindings]", 'account = "binance_testnet_spot"', f'market = ["{market}"]'])
    if mode == "live":
        sections.extend([
            "",
            "[bindings]",
            'account = "binance_live_spot"',
            f'market = ["{market}"]',
            'execution = "binance_live_spot"',
            "",
            "[live]",
            'provider = "binance"',
            'execution_driver = "binance-live"',
            'binding_id = "live-runtime-binding"',
            'recovery_binding_id = "live-recovery"',
        ])
        if bind_provider:
            sections.append("bind_provider = true")
        sections.extend([
            "",
            "[evidence]",
            'readiness = "governance:readiness/test-live.json"',
            'promotion = "governance:promotion/test-live.json"',
        ])
    if extra:
        sections.extend(["", extra.strip()])
    path.write_text("\n".join(sections).rstrip() + "\n", encoding="utf-8")
    return path


def _write_default_workspace_code(root: Path) -> None:
    (root / "workspace_code.py").write_text(
        "\n".join([
            "def build_workspace(ws, params):",
            "    profile = params.get('workspace_profile', 'alpha')",
            "    market = ws.attachments.use_profile(profile).as_ohlcv(params.get('market', 'bars'), required=False)",
            "    feature = ws.features.momentum(name='momentum', source=market, window=int(params.get('window', '20')))",
            "    return ws.project(market=(market,), features=(feature,))",
        ])
        + "\n",
        encoding="utf-8",
    )


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


class WorkspaceTests(unittest.TestCase):
    def test_workspace_open_or_create_writes_project_workspace_manifest(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Workspace Desk")

            workspace = Workspace.open_or_create("alpha", start=root)
            binding = workspace.attach("bars", dataset="bars.us.equity.1d", view="both")

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

            attached = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kairospy",
                    "--format",
                    "json",
                    "workspace",
                    "attach",
                    "alpha",
                    "--name",
                    "bars",
                    "--dataset",
                    "bars.us.equity.1d",
                    "--view",
                    "both",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            payload = json.loads(attached.stdout)
            self.assertEqual(payload["attachment"]["name"], "bars")
            self.assertEqual(payload["attachment"]["dataset"], "bars.us.equity.1d")
            self.assertEqual(payload["attachment"]["metadata"]["view"], "both")

    def test_run_start_combines_workspace_code_and_standard_strategy(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Workspace Run")
            Workspace.open_or_create("alpha", start=root).attach("bars", dataset="bars.us.equity.1d", view="both")
            (root / "my_strategy.py").write_text(
                "\n".join(
                    [
                        "class Strategy:",
                        "    strategy_id = 'context-only-strategy'",
                        "    def on_start(self, context):",
                        "        return ()",
                        "    def on_market(self, context):",
                        "        return ({'action': 'hold', 'binding': context.market.data_binding, 'features': len(context.features.values)},)",
                        "    def on_fill(self, fill, context):",
                        "        return ()",
                        "    def on_end(self, context):",
                        "        return ()",
                        "",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
            run_config = _write_run_config(
                root,
                "workspace-run.toml",
                mode="backtest",
                strategy="my_strategy:Strategy",
                params={"fast": "20"},
            )

            started = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kairospy",
                    "--format",
                    "json",
                    "run",
                    "start",
                    "--config",
                    str(run_config),
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

            payload = json.loads(started.stdout)
            run_root = Path(payload["run_workspace"])
            self.assertEqual(payload["workspace"]["name"], "workspace_code:build_workspace")
            self.assertEqual(payload["strategy"]["workspace_entrypoint"], "workspace_code:build_workspace")
            self.assertEqual(payload["strategy"]["entrypoint"], "my_strategy:Strategy")
            self.assertEqual(payload["strategy"]["params"]["fast"], "20")
            self.assertEqual(run_root.parent.resolve(), (root / ".kairos" / "run").resolve())
            self.assertTrue((run_root / "manifest.json").exists())
            self.assertTrue((run_root / "workspace_snapshot.json").exists())
            self.assertTrue((run_root / "projection.json").exists())
            self.assertTrue((run_root / "resolved_config.toml").exists())
            manifest = json.loads((run_root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["project_config"]["hash"], _file_sha256(root / "kairos.toml"))
            self.assertEqual(manifest["run_config"]["hash"], _file_sha256(run_config))
            self.assertEqual(manifest["run_config"]["resolved_config_artifact"], str(run_root / "resolved_config.toml"))
            self.assertEqual(manifest["strategy"]["hash"], payload["strategy"]["hash"])
            self.assertTrue(manifest["params_hash"])
            self.assertTrue(manifest["config_hash"])
            self.assertIn("account_binding_hash", manifest["bindings"])
            self.assertIn("readiness_ref", manifest["guards"])
            decisions = json.loads((run_root / "artifacts" / "decisions.json").read_text(encoding="utf-8"))
            self.assertEqual(decisions[0]["binding"], "workspace_projection")
            self.assertEqual(decisions[0]["features"], 1)

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

    def test_run_start_paper_uses_runtime_launcher_and_governance_artifact(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Workspace Paper Run")
            Workspace.open_or_create("alpha", start=root).attach("bars", dataset="bars.us.equity.1d", view="both")
            (root / "paper_strategy.py").write_text(
                "\n".join([
                    "class Strategy:",
                    "    strategy_id = 'paper-strategy'",
                    "    def on_start(self, context): return ()",
                    "    def on_market(self, context): return ({'action': 'hold', 'mode': 'paper'},)",
                    "    def on_fill(self, fill, context): return ()",
                    "    def on_end(self, context): return ()",
                ]) + "\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
            run_config = _write_run_config(
                root,
                "paper-run.toml",
                mode="paper",
                strategy="paper_strategy:Strategy",
            )

            started = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kairospy",
                    "--format",
                    "json",
                    "run",
                    "start",
                    "--config",
                    str(run_config),
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
            workspace.attach("bars", dataset="bars.us.equity.1d", view="both")
            (root / "live_strategy.py").write_text(
                "\n".join([
                    "class Strategy:",
                    "    strategy_id = 'live-strategy'",
                    "    def on_start(self, context): return ()",
                    "    def on_market(self, context): return ({'action': 'hold', 'mode': 'live'},)",
                    "    def on_fill(self, fill, context): return ()",
                    "    def on_end(self, context): return ()",
                ]) + "\n",
                encoding="utf-8",
            )
            workspace_hash = stable_artifact_hash(workspace.snapshot())
            strategy_hash = stable_artifact_hash({
                "entrypoint": "live_strategy:Strategy",
                "module": "live_strategy",
                "callable": "Strategy",
            })
            run_config = _write_run_config(
                root,
                "live-run.toml",
                mode="live",
                strategy="live_strategy:Strategy",
            )
            config_hash = stable_artifact_hash({"params": {}, "run_config_hash": _file_sha256(run_config)})
            config_path = root / "kairos.toml"
            config_path.write_text(
                config_path.read_text(encoding="utf-8").replace(
                    "live_trading_enabled = false",
                    "live_trading_enabled = true",
                ),
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
                    "--config",
                    str(run_config),
                    "--confirm-live",
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
            workspace.attach("bars", dataset="bars.us.equity.1d", view="both")
            (root / "live_strategy.py").write_text(
                "\n".join([
                    "class Strategy:",
                    "    strategy_id = 'live-strategy'",
                    "    def on_start(self, context): return ()",
                    "    def on_market(self, context): return ({'action': 'hold', 'mode': 'live'},)",
                    "    def on_fill(self, fill, context): return ()",
                    "    def on_end(self, context): return ()",
                ]) + "\n",
                encoding="utf-8",
            )
            workspace_hash = stable_artifact_hash(workspace.snapshot())
            strategy_hash = stable_artifact_hash({
                "entrypoint": "live_strategy:Strategy",
                "module": "live_strategy",
                "callable": "Strategy",
            })
            run_config = _write_run_config(
                root,
                "live-provider.toml",
                mode="live",
                strategy="live_strategy:Strategy",
                bind_provider=True,
            )
            config_hash = stable_artifact_hash({"params": {}, "run_config_hash": _file_sha256(run_config)})
            config_path = root / "kairos.toml"
            config_path.write_text(
                config_path.read_text(encoding="utf-8").replace(
                    "live_trading_enabled = false",
                    "live_trading_enabled = true",
                ),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
            env["KAIROS_BINANCE_TRADING_LIVE_SPOT_API_KEY"] = "test-key"
            env["KAIROS_BINANCE_TRADING_LIVE_SPOT_API_SECRET"] = "test-secret"

            started = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kairospy",
                    "--format",
                    "json",
                    "run",
                    "start",
                    "--config",
                    str(run_config),
                    "--confirm-live",
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
            workspace.attach("ticks", dataset=dataset_id, view="live")
            (root / "live_strategy.py").write_text(
                "\n".join([
                    "class Strategy:",
                    "    strategy_id = 'live-strategy'",
                    "    def on_start(self, context): return ()",
                    "    def on_market(self, context): return ({'action': 'hold', 'mode': 'live'},)",
                    "    def on_fill(self, fill, context): return ()",
                    "    def on_end(self, context): return ()",
                ]) + "\n",
                encoding="utf-8",
            )
            workspace_hash = stable_artifact_hash(workspace.snapshot())
            strategy_hash = stable_artifact_hash({
                "entrypoint": "live_strategy:Strategy",
                "module": "live_strategy",
                "callable": "Strategy",
            })
            run_config = _write_run_config(
                root,
                "live-market.toml",
                mode="live",
                strategy="live_strategy:Strategy",
                bind_provider=True,
                market="ticks",
                extra="\n".join([
                    "[bindings.live_views.ticks]",
                    f'dataset = "{dataset_id}"',
                    f'live_view_id = "{live_view_id}"',
                    'journal_root = ".kairos/data/live-journals"',
                ]),
            )
            config_hash = stable_artifact_hash({"params": {}, "run_config_hash": _file_sha256(run_config)})
            config_path = root / "kairos.toml"
            config_path.write_text(
                config_path.read_text(encoding="utf-8").replace(
                    "live_trading_enabled = false",
                    "live_trading_enabled = true",
                ),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
            env["KAIROS_BINANCE_TRADING_LIVE_SPOT_API_KEY"] = "test-key"
            env["KAIROS_BINANCE_TRADING_LIVE_SPOT_API_SECRET"] = "test-secret"

            started = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kairospy",
                    "--format",
                    "json",
                    "run",
                    "start",
                    "--config",
                    str(run_config),
                    "--confirm-live",
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
            self.assertFalse(governance["config"]["run_request"]["metadata"]["market_services_supervised"])
            services = payload["runtime_launch"]["services"]["services"]
            service_names = [item["name"] for item in services]
            self.assertTrue(any(name.startswith("outbox-dispatcher:") for name in service_names))
            self.assertTrue(any(name.startswith("risk-monitor:") for name in service_names))
            self.assertTrue(all(item["status"] == "stopped" for item in services))

    def test_run_start_live_run_config_strategy_spec_binds_stop_controller_on_shutdown(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Workspace Live Stop Policy Binding")
            _write_binance_reference_catalog(root / ".kairos" / "data" / "reference" / "catalog.json")
            workspace = Workspace.open_or_create("alpha", start=root)
            (root / "live_strategy.py").write_text(
                "\n".join([
                    "from decimal import Decimal",
                    "from kairospy.reference import ProductType",
                    "from kairospy.strategy import StrategyLifecycle, StrategySpec",
                    "",
                    "class Strategy:",
                    "    strategy_id = 'live-stop-strategy'",
                    "    def on_start(self, context): return ()",
                    "    def on_market(self, context): return ({'action': 'hold', 'mode': 'live'},)",
                    "    def on_fill(self, fill, context): return ()",
                    "    def on_end(self, context): return ()",
                    "",
                    "def spec():",
                    "    return StrategySpec(",
                    "        'live-stop-strategy', '1.0.0', StrategyLifecycle.LIVE_LIMITED,",
                    "        (ProductType.CRYPTO_SPOT,), ('target_position',), ('momentum',), ('price',),",
                    "        (('instrument', 'BTC-USDT'),), ('price',), (('threshold', '0'),),",
                    "        (('target', 'position'),), ('enter',), ('exit',), ('manual',),",
                    "        Decimal('0.01'), ('bars',), ('limit_orders',), 'strategy-evidence'",
                    "    )",
                ]) + "\n",
                encoding="utf-8",
            )
            workspace_hash = stable_artifact_hash(workspace.snapshot())
            strategy_hash = stable_artifact_hash({
                "entrypoint": "live_strategy:Strategy",
                "module": "live_strategy",
                "callable": "Strategy",
            })
            run_config = _write_run_config(
                root,
                "live-stop-policy.toml",
                mode="live",
                strategy="live_strategy:Strategy",
                bind_provider=True,
                extra="\n".join([
                    "[strategy]",
                    'spec = "live_strategy:spec"',
                ]),
            )
            config_hash = stable_artifact_hash({"params": {}, "run_config_hash": _file_sha256(run_config)})
            config_path = root / "kairos.toml"
            config_path.write_text(
                config_path.read_text(encoding="utf-8").replace(
                    "live_trading_enabled = false",
                    "live_trading_enabled = true",
                ),
                encoding="utf-8",
            )

            from kairospy.infrastructure.configuration import KairosProjectConfig
            from kairospy.integrations import LiveProviderPorts
            from kairospy.integrations.connectors.simulated import SimulatedExecutionAccountGateway
            from kairospy.integrations.ports import Environment
            from kairospy.identity import AccountRef, AccountType, InstitutionId
            from kairospy.runtime.run_config import load_run_config
            from kairospy.surface.product import _launch_workspace_live_run, _strategy_run_result_from_workspace_execution

            account = AccountRef(InstitutionId("binance"), "main", AccountType.CRYPTO_SPOT)
            gateway = SimulatedExecutionAccountGateway(VenueId("binance"), account, environment=Environment.LIVE)
            provider_ports = LiveProviderPorts(
                "binance",
                "binance-live",
                account,
                gateway,
                gateway,
                None,
            )

            with patch("kairospy.integrations.live_ports.build_live_provider_ports", return_value=provider_ports):
                runtime_launch, run_result = _launch_workspace_live_run(
                    KairosProjectConfig.discover(root),
                    root / ".kairos" / "run" / "live-stop-policy",
                    "run_live_stop_policy",
                    workspace.name,
                    workspace_hash,
                    strategy_hash,
                    config_hash,
                    {},
                    lambda _prepared: _strategy_run_result_from_workspace_execution({"decisions": []}),
                    run_config=load_run_config(run_config),
                    workspace_snapshot=workspace.snapshot(),
                    confirm_live=True,
            )

            self.assertEqual(runtime_launch["stop_report"]["strategy_id"], "live-stop-strategy")
            self.assertEqual(runtime_launch["stop_report"]["reason"], "crash")
            self.assertEqual(runtime_launch["stop_report"]["action"], "cancel_orders")
            self.assertEqual(runtime_launch["stop_report"]["cancelled_client_order_ids"], [])
            governance = json.loads(Path(run_result.artifact_refs[-1]).read_text(encoding="utf-8"))
            metadata = governance["config"]["run_request"]["metadata"]["stop_policy"]
            self.assertEqual(metadata["strategy_id"], "live-stop-strategy")
            self.assertTrue(metadata["controller_bound"])
            self.assertEqual(
                governance["execution"]["runtime_launch"]["stop_report"]["strategy_id"],
                "live-stop-strategy",
            )

    def test_run_live_start_uses_run_config_to_bind_services_and_stop_controller(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Workspace Live Daemon RunConfig")
            _write_binance_reference_catalog(root / ".kairos" / "data" / "reference" / "catalog.json")
            Workspace.open_or_create("alpha", start=root)
            (root / "live_strategy.py").write_text(
                "\n".join([
                    "from decimal import Decimal",
                    "from kairospy.reference import ProductType",
                    "from kairospy.strategy import StrategyLifecycle, StrategySpec",
                    "",
                    "class Strategy:",
                    "    strategy_id = 'live-daemon-strategy'",
                    "    def on_start(self, context): return ()",
                    "    def on_market(self, context): return ({'action': 'hold', 'mode': 'live'},)",
                    "    def on_fill(self, fill, context): return ()",
                    "    def on_end(self, context): return ()",
                    "",
                    "def spec():",
                    "    return StrategySpec(",
                    "        'live-daemon-strategy', '1.0.0', StrategyLifecycle.LIVE_LIMITED,",
                    "        (ProductType.CRYPTO_SPOT,), ('target_position',), ('momentum',), ('price',),",
                    "        (('instrument', 'BTC-USDT'),), ('price',), (('threshold', '0'),),",
                    "        (('target', 'position'),), ('enter',), ('exit',), ('manual',),",
                    "        Decimal('0.01'), ('bars',), ('limit_orders',), 'strategy-evidence'",
                    "    )",
                ]) + "\n",
                encoding="utf-8",
            )
            run_config = _write_run_config(
                root,
                "live-daemon.toml",
                mode="live",
                strategy="live_strategy:Strategy",
                bind_provider=True,
                extra="\n".join([
                    "[strategy]",
                    'spec = "live_strategy:spec"',
                ]),
            )
            config_path = root / "kairos.toml"
            config_path.write_text(
                config_path.read_text(encoding="utf-8").replace(
                    "live_trading_enabled = false",
                    "live_trading_enabled = true",
                ),
                encoding="utf-8",
            )

            from kairospy.integrations import LiveProviderPorts
            from kairospy.integrations.connectors.simulated import SimulatedExecutionAccountGateway
            from kairospy.integrations.ports import Environment
            from kairospy.identity import AccountRef, AccountType, InstitutionId
            from kairospy.surface import product as product_surface

            account = AccountRef(InstitutionId("binance"), "main", AccountType.CRYPTO_SPOT)
            gateway = SimulatedExecutionAccountGateway(VenueId("binance"), account, environment=Environment.LIVE)
            provider_ports = LiveProviderPorts(
                "binance",
                "binance-live",
                account,
                gateway,
                gateway,
                gateway,
            )

            with patch("kairospy.integrations.live_ports.build_live_provider_ports", return_value=provider_ports):
                cwd = Path.cwd()
                os.chdir(root)
                try:
                    started = product_surface.run_live(SimpleNamespace(
                        live_action="start",
                        run_id="venue-a-live",
                        config=run_config,
                        confirm_live=True,
                        duration_seconds=0,
                        poll_seconds=0.01,
                        param=[],
                    ))
                    status = product_surface.run_live(SimpleNamespace(
                        live_action="status",
                        run_id="venue-a-live",
                        stale_after_seconds=5.0,
                    ))
                finally:
                    os.chdir(cwd)

            self.assertEqual(started["status"], "stopped")
            self.assertEqual(started["run_config"]["provider_binding"], True)
            self.assertEqual(started["run_config"]["stop_policy"]["strategy_id"], "live-daemon-strategy")
            self.assertEqual(started["stop_report"]["strategy_id"], "live-daemon-strategy")
            self.assertEqual(started["stop_report"]["reason"], "scheduled")
            self.assertEqual(started["stop_report"]["action"], "cancel_orders")
            self.assertTrue(any(
                service["name"] == "strategy-run:venue-a-live"
                for service in started["services"]
            ))
            self.assertEqual(status["run_config"]["stop_policy"]["strategy_id"], "live-daemon-strategy")
            self.assertEqual(status["stop_report"]["reason"], "scheduled")

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
            workspace.attach("ticks", dataset=dataset_id, view="live")
            workspace_hash = stable_artifact_hash(workspace.snapshot())
            strategy_hash = stable_artifact_hash({
                "entrypoint": "live_strategy:Strategy",
                "module": "live_strategy",
                "callable": "Strategy",
            })
            run_config = _write_run_config(
                root,
                "live-supervised.toml",
                mode="live",
                strategy="live_strategy:Strategy",
                bind_provider=True,
                market="ticks",
                extra="\n".join([
                    "[bindings.live_views.ticks]",
                    f'dataset = "{dataset_id}"',
                    f'live_view_id = "{live_view_id}"',
                    "supervise_services = true",
                ]),
            )
            config_hash = stable_artifact_hash({"params": {}, "run_config_hash": _file_sha256(run_config)})
            config_path = root / "kairos.toml"
            config_path.write_text(
                config_path.read_text(encoding="utf-8").replace(
                    "live_trading_enabled = false",
                    "live_trading_enabled = true",
                ),
                encoding="utf-8",
            )

            from kairospy.infrastructure.configuration import KairosProjectConfig
            from kairospy.integrations import LiveMarketEventSourceBinding, LiveProviderPorts
            from kairospy.integrations.connectors.simulated import SimulatedExecutionAccountGateway
            from kairospy.integrations.ports import Environment
            from kairospy.identity import AccountRef, AccountType, InstitutionId
            from kairospy.market.stream import IterableEventSource
            from kairospy.runtime import ManagedServiceSpec
            from kairospy.runtime.run_config import load_run_config
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
                    run_config=load_run_config(run_config),
                    workspace_snapshot=workspace.snapshot(),
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
                    "--config",
                    "configs/runs/backtest.example.toml",
                    "--snapshot",
                    "old@1.0.0",
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

    def test_run_start_help_requires_run_config_not_workspace_flags(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "kairospy", "run", "start", "--help"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": os.getcwd() + os.pathsep + os.environ.get("PYTHONPATH", "")},
        )

        self.assertIn("--config", completed.stdout)
        self.assertNotIn("--workspace", completed.stdout)
        self.assertNotIn("--entrypoint", completed.stdout)
        self.assertNotIn("--mode", completed.stdout)

    def test_run_config_validate_and_explain_use_run_config_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Run Config Cli")
            run_config = root / "configs" / "runs" / "backtest.example.toml"
            env = {**os.environ, "PYTHONPATH": os.getcwd() + os.pathsep + os.environ.get("PYTHONPATH", "")}

            validated = subprocess.run(
                [sys.executable, "-m", "kairospy", "--format", "json", "run", "config", "validate", str(run_config)],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            explained = subprocess.run(
                [sys.executable, "-m", "kairospy", "--format", "json", "run", "config", "explain", str(run_config)],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertTrue(json.loads(validated.stdout)["valid"])
            self.assertEqual(json.loads(explained.stdout)["run"]["mode"], "backtest")


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
