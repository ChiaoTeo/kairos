from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from kairospy import initialize_project


class WorkspaceProjectionFlowTests(unittest.TestCase):
    def test_workspace_attach_and_inspect_code(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Projection Flow")
            _write_workspace_code(root)
            env = _env()

            _run_cli(root, env, "workspace", "create", "alpha")
            attach = _run_cli(
                root,
                env,
                "workspace",
                "attach",
                "alpha",
                "--name",
                "bars_raw",
                "--dataset",
                "market.demo.bars",
                "--view",
                "both",
                "--instrument",
                "BTC",
            )
            self.assertEqual(attach["attachment"]["metadata"]["view"], "both")

            inspected = _run_cli(
                root,
                env,
                "workspace",
                "inspect-code",
                "my_workspace:build_workspace",
                "--param",
                "workspace_profile=alpha",
            )
            self.assertEqual(inspected["entrypoint"], "my_workspace:build_workspace")
            self.assertEqual(inspected["projection"]["attachments"]["bars_raw"]["dataset"], "market.demo.bars")
            self.assertEqual([item["name"] for item in inspected["nodes"]], ["bars", "momentum_1d"])
            self.assertTrue(inspected["preflight"]["passed"])

    def test_workspace_projection_preserves_stream_from_profile(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Projection Stream")
            _write_workspace_code(root)
            env = _env()

            _run_cli(root, env, "workspace", "create", "alpha")
            _run_cli(
                root,
                env,
                "workspace",
                "add",
                "alpha",
                "binance_swap_btcusdt.ohlcv_1h",
                "--name",
                "bars_raw",
                "--view",
                "both",
            )

            inspected = _run_cli(
                root,
                env,
                "workspace",
                "inspect-code",
                "my_workspace:build_workspace",
                "--param",
                "workspace_profile=alpha",
                "--param",
                "market=binance_swap_btcusdt.ohlcv_1h",
            )

            attachment = inspected["projection"]["attachments"]["bars_raw"]
            self.assertEqual(attachment["stream"], "binance_swap_btcusdt.ohlcv_1h")
            self.assertEqual(attachment["dataset"], "market.ohlcv.crypto.binance.usdm-perpetual.btc-usdt.1h")
            self.assertEqual(inspected["nodes"][0]["stream"], "binance_swap_btcusdt.ohlcv_1h")
            self.assertTrue(inspected["preflight"]["passed"])

    def test_workspace_preflight_reports_missing_optional_and_mode_mismatch(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Projection Preflight")
            (root / "live_only_workspace.py").write_text(
                "\n".join([
                    "def build_workspace(ws, params):",
                    "    ws.attach(name='book_live', dataset='market.demo.book', view='live')",
                    "    book = ws.use('book_live').as_orderbook(name='book')",
                    "    missing = ws.use('optional_bars').as_ohlcv(name='optional_bars', required=False)",
                    "    return ws.project(market=(book, missing))",
                ])
                + "\n",
                encoding="utf-8",
            )
            _write_strategy_code(root)
            env = _env()

            inspected = _run_cli(
                root,
                env,
                "workspace",
                "inspect-code",
                "live_only_workspace:build_workspace",
                "--mode",
                "backtest",
            )

            self.assertFalse(inspected["preflight"]["passed"])
            self.assertEqual(
                [item["code"] for item in inspected["preflight"]["issues"]],
                ["view_not_available", "missing_attachment"],
            )

            config = root / "configs" / "runs" / "bad-backtest.toml"
            config.write_text(
                "\n".join([
                    "[run]",
                    'name = "bad-backtest"',
                    'mode = "backtest"',
                    'workspace = "live_only_workspace:build_workspace"',
                    'strategy = "my_strategy:Strategy"',
                ])
                + "\n",
                encoding="utf-8",
            )
            rejected = subprocess.run(
                [sys.executable, "-m", "kairospy", "--format", "json", "run", "start", "--config", str(config)],
                cwd=root,
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("workspace preflight failed", rejected.stderr)

    def test_run_start_defaults_to_empty_workspace_and_strategy(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Empty Run")
            env = _env()
            config = root / "configs" / "runs" / "empty.toml"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text('[run]\nname = "empty"\nmode = "backtest"\n', encoding="utf-8")

            validated = _run_cli(root, env, "run", "config", "validate", str(config))
            self.assertTrue(validated["valid"])

            started = _run_cli(root, env, "run", "start", "--config", str(config))
            self.assertEqual(started["decisions_count"], 0)
            self.assertEqual(started["strategy"]["workspace_entrypoint"], "kairospy.workspace.defaults:empty_workspace")
            self.assertEqual(started["strategy"]["entrypoint"], "kairospy.workspace.defaults:EmptyStrategy")

    def test_run_start_combines_workspace_code_and_strategy_code(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Workspace Strategy Run")
            _write_workspace_code(root)
            _write_strategy_code(root)
            env = _env()
            _run_cli(root, env, "workspace", "create", "alpha")
            _run_cli(
                root,
                env,
                "workspace",
                "attach",
                "alpha",
                "--name",
                "bars_raw",
                "--dataset",
                "market.demo.bars",
                "--view",
                "both",
            )
            config = root / "configs" / "runs" / "demo.toml"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(
                "\n".join([
                    "[run]",
                    'name = "demo"',
                    'mode = "backtest"',
                    'workspace = "my_workspace:build_workspace"',
                    'strategy = "my_strategy:Strategy"',
                    "",
                    "[params]",
                    'workspace_profile = "alpha"',
                ])
                + "\n",
                encoding="utf-8",
            )

            started = _run_cli(root, env, "run", "start", "--config", str(config))

            self.assertEqual(started["decisions_count"], 1)
            self.assertEqual(started["strategy"]["workspace_entrypoint"], "my_workspace:build_workspace")
            self.assertEqual(started["strategy"]["entrypoint"], "my_strategy:Strategy")
            projection = json.loads(Path(started["artifacts"]["projection"]).read_text(encoding="utf-8"))
            self.assertEqual(projection["features"][0]["name"], "momentum_1d")

    def test_funding_arb_acceptance_run_emits_risk_and_treasury_plan(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Funding Arb Acceptance")
            env = _env()
            _run_cli(root, env, "workspace", "create", "funding-arb")
            for name, dataset, view, instrument in (
                ("hl_perp_mark", "market.hyperliquid.perp.mark.btc", "both", "BTC-PERP"),
                ("hl_funding", "market.hyperliquid.perp.funding.btc", "both", "BTC-PERP"),
                ("hl_orderbook", "market.hyperliquid.perp.book.btc", "live", "BTC-PERP"),
                ("binance_spot_book", "market.binance.spot.book.btcusdt", "live", "BTCUSDT"),
            ):
                _run_cli(
                    root,
                    env,
                    "workspace",
                    "attach",
                    "funding-arb",
                    "--name",
                    name,
                    "--dataset",
                    dataset,
                    "--view",
                    view,
                    "--instrument",
                    instrument,
                )

            config = Path(os.getcwd()) / "examples" / "configs" / "runs" / "funding-arb-paper.toml"
            live_preflight_config = Path(os.getcwd()) / "examples" / "configs" / "runs" / "funding-arb-live-preflight.toml"
            inspected = _run_cli(
                root,
                env,
                "workspace",
                "inspect-code",
                "examples.workspace.funding_arb:build_workspace",
                "--param",
                "workspace_profile=funding-arb",
            )
            live_inspected = _run_cli(
                root,
                env,
                "workspace",
                "inspect-code",
                "examples.workspace.funding_arb:build_workspace",
                "--mode",
                "live",
                "--param",
                "workspace_profile=funding-arb",
            )
            live_validated = _run_cli(root, env, "run", "config", "validate", str(live_preflight_config))
            started = _run_cli(root, env, "run", "start", "--config", str(config))

            feature_names = [item["name"] for item in inspected["nodes"] if str(item.get("kind", "")).startswith("feature:")]
            self.assertEqual(
                feature_names,
                ["hl_bn_basis", "expected_funding_carry", "cross_venue_liquidity", "hedge_error"],
            )
            decisions = json.loads((Path(started["run_workspace"]) / "artifacts" / "decisions.json").read_text(encoding="utf-8"))
            decision = decisions[0]
            self.assertEqual(decision["legs"]["short"]["venue"], "hyperliquid")
            self.assertEqual(decision["legs"]["long"]["venue"], "binance")
            self.assertIn("margin_buffer_sufficient", decision["risk_checks"])
            self.assertEqual(decision["orders"][0]["blocked_until"], "live_execution_keys_and_risk_inputs_verified")
            self.assertEqual(decision["treasury_transfer_intent"]["to"], "hyperliquid")
            self.assertEqual(decision["decision"], "hold_until_live_risk_inputs_are_bound")
            self.assertTrue(live_inspected["preflight"]["passed"])
            self.assertTrue(live_validated["valid"])
            self.assertTrue(live_validated["guards"]["manual_hyperliquid_execution_required"])


def _write_workspace_code(root: Path) -> None:
    (root / "my_workspace.py").write_text(
        "\n".join([
            "def build_workspace(ws, params):",
            "    ws.attachments.use_profile(params.get('workspace_profile', 'alpha'))",
            "    bars = ws.use('bars_raw').as_ohlcv(name='bars', warmup='1d')",
            "    momentum = ws.features.momentum(name='momentum_1d', source=bars, lookback='1d')",
            "    return ws.project(market=[bars], features=[momentum])",
        ])
        + "\n",
        encoding="utf-8",
    )


def _write_strategy_code(root: Path) -> None:
    (root / "my_strategy.py").write_text(
        "\n".join([
            "class Strategy:",
            "    strategy_id = 'demo-strategy'",
            "    def on_start(self, context): return ()",
            "    def on_market(self, context):",
            "        factor = context.features.factor('momentum_1d')",
            "        return ({'factor': factor.feature_id},)",
            "    def on_fill(self, fill, context): return ()",
            "    def on_end(self, context): return ()",
        ])
        + "\n",
        encoding="utf-8",
    )


def _run_cli(root: Path, env: dict[str, str], *args: str) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, "-m", "kairospy", "--format", "json", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return json.loads(result.stdout)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
    return env
