from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from kairospy.infrastructure.configuration import KairosProjectConfig, set_config_value, unset_config_value
from kairospy.integrations.config import (
    resolve_account_binding,
    resolve_binance_trading_credentials,
    resolve_hyperliquid_trading_credentials,
    resolve_massive_marketdata_config,
    resolve_provider_service_config,
)
from kairospy.surface.project import initialize_project


class KairosProjectConfigurationTests(unittest.TestCase):
    def test_project_config_discovers_resolves_and_redacts_provider_values(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Config Desk")

            env = {"KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY": "secret"}
            old = os.environ.copy()
            try:
                os.environ.pop("KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY", None)
                with self.assertRaisesRegex(Exception, "Massive market data credential is missing"):
                    resolve_massive_marketdata_config(KairosProjectConfig.discover(root))
                os.environ.update(env)
                config = KairosProjectConfig.discover(root / "notebooks")
                self.assertEqual(config.root, root.resolve())
                self.assertEqual(resolve_massive_marketdata_config(config).api_key, "secret")
                self.assertEqual(
                    config.to_redacted_dict()["credentials"]["massive_marketdata_primary"]["api_key"],
                    "env:KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY",
                )
                set_config_value(config.path, "credentials.hyperliquid_trading_live_perp.private_key", "raw-private-key")
                hyper_config = KairosProjectConfig.discover(root)
                self.assertEqual(
                    hyper_config.to_redacted_dict()["credentials"]["hyperliquid_trading_live_perp"]["private_key"],
                    "***",
                )
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_project_config_resolves_hyperliquid_live_credential_and_account_binding(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Hyperliquid Config Desk")
            old = os.environ.copy()
            try:
                os.environ["KAIROS_HYPERLIQUID_LIVE_PRIVATE_KEY"] = "test-private-key"
                os.environ["KAIROS_HYPERLIQUID_LIVE_ACCOUNT_ADDRESS"] = "0xabc"
                config = KairosProjectConfig.discover(root)
                credentials = resolve_hyperliquid_trading_credentials(config)
                account = resolve_account_binding(config, "hyperliquid_live_perp")
            finally:
                os.environ.clear()
                os.environ.update(old)

            self.assertEqual(credentials.private_key, "test-private-key")
            self.assertEqual(credentials.account_address, "0xabc")
            self.assertEqual(account.provider, "hyperliquid")
            self.assertEqual(account.account_ref, "hyperliquid:derivatives:main")
            self.assertEqual(account.allowed_products, ("perpetual",))

    def test_project_config_loads_dotenv_without_overriding_existing_environment(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Dotenv Desk")
            (root / ".env").write_text(
                "KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY=dotenv-secret\nQUOTED=\"quoted value\"\n",
                encoding="utf-8",
            )
            old = os.environ.copy()
            os.environ.pop("KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY", None)
            os.environ.pop("QUOTED", None)
            try:
                self.assertEqual(
                    resolve_massive_marketdata_config(KairosProjectConfig.discover(root)).api_key,
                    "dotenv-secret",
                )
                self.assertEqual(os.environ["QUOTED"], "quoted value")
                os.environ["KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY"] = "shell-secret"
                self.assertEqual(
                    resolve_massive_marketdata_config(KairosProjectConfig.discover(root)).api_key,
                    "shell-secret",
                )
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_cli_loads_cwd_dotenv_before_parser_defaults(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            lake = root / "lake-from-dotenv"
            (root / ".env").write_text(f"KAIROSPY_LAKE_ROOT={lake}\n", encoding="utf-8")
            old_environ = os.environ.copy()
            old_cwd = Path.cwd()
            captured = {}
            try:
                os.environ.pop("KAIROSPY_LAKE_ROOT", None)
                os.chdir(root)
                from kairospy.surface.cli import main as cli_main

                with patch.object(cli_main, "_providers", side_effect=lambda args: captured.setdefault("lake_root", args.lake_root) and 0):
                    self.assertEqual(cli_main.main(["providers", "list"]), 0)
                self.assertEqual(captured["lake_root"], str(lake))
            finally:
                os.chdir(old_cwd)
                os.environ.clear()
                os.environ.update(old_environ)

    def test_integration_config_resolves_provider_service_and_account_binding(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Resolver Desk")
            old = os.environ.copy()
            try:
                os.environ["KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_KEY"] = "test-key"
                os.environ["KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_SECRET"] = "test-secret"
                config = KairosProjectConfig.discover(root)
                service = resolve_provider_service_config(config, "binance", "execution_testnet")
                account = resolve_account_binding(config, "binance_testnet_spot")
                credentials = resolve_binance_trading_credentials(config, "testnet")

                self.assertEqual(service.credential, "binance_trading_testnet_spot")
                self.assertEqual(account.credential, "binance_trading_testnet_spot")
                self.assertEqual(account.provider, "binance")
                self.assertEqual(credentials.api_key, "test-key")
                self.assertEqual(credentials.api_secret, "test-secret")
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_accounts_doctor_checks_account_binding_without_general_doctor_noise(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Accounts Desk")
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
            env["KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_KEY"] = "test-key"
            env["KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_SECRET"] = "test-secret"

            doctor = subprocess.run(
                [
                    sys.executable, "-m", "kairospy", "--format", "json",
                    "accounts", "doctor", "binance_testnet_spot",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            payload = json.loads(doctor.stdout)

            self.assertEqual(payload["status"], "available")
            self.assertEqual(payload["account"], "binance_testnet_spot")
            self.assertEqual(payload["provider"], "binance")
            self.assertEqual(payload["checks"]["account_query"], "not_run")
            self.assertEqual(payload["issues"], [])

    def test_accounts_doctor_checks_hyperliquid_live_perp_without_exposing_private_key(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Hyperliquid Accounts Desk")
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
            env["KAIROS_HYPERLIQUID_LIVE_PRIVATE_KEY"] = "test-private-key"
            env["KAIROS_HYPERLIQUID_LIVE_ACCOUNT_ADDRESS"] = "0xabc"

            doctor = subprocess.run(
                [
                    sys.executable, "-m", "kairospy", "--format", "json",
                    "accounts", "doctor", "hyperliquid_live_perp",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            payload = json.loads(doctor.stdout)

            self.assertEqual(payload["status"], "available")
            self.assertEqual(payload["account"], "hyperliquid_live_perp")
            self.assertEqual(payload["provider"], "hyperliquid")
            self.assertEqual(payload["allowed_products"], ["perpetual"])
            self.assertEqual(payload["checks"]["account_query"], "not_run")
            self.assertEqual(payload["issues"], [])
            self.assertNotIn("test-private-key", doctor.stdout)
            self.assertEqual(
                {(item["field"], item["provided"]) for item in payload["credential_refs"]},
                {("private_key", True), ("account_address", True)},
            )

    def test_data_bootstrap_import_is_safe_from_cold_process(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [
                sys.executable, "-c",
                "from kairospy.integrations.data_products.bootstrap import register_default_products; "
                "from kairospy.integrations.connectors.binance import BinanceRuntimeFeedFactory; "
                "from kairospy.workspace import Workspace; "
                "print(register_default_products.__name__, BinanceRuntimeFeedFactory.__name__, Workspace.__name__)",
            ],
            cwd=os.getcwd(),
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertIn("register_default_products BinanceRuntimeFeedFactory Workspace", result.stdout)

    def test_set_and_unset_config_value_preserves_valid_toml(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Config Desk")
            path = root / "kairos.toml"

            set_config_value(path, "credentials.massive_marketdata_primary.api_key", "env:KAIROSPY_MASSIVE_KEY")
            config = KairosProjectConfig.load(path)
            self.assertEqual(config.get("credentials.massive_marketdata_primary.api_key"), "env:KAIROSPY_MASSIVE_KEY")

            self.assertTrue(unset_config_value(path, "credentials.massive_marketdata_primary.api_key"))
            config = KairosProjectConfig.load(path)
            self.assertIsNone(config.get("credentials.massive_marketdata_primary.api_key"))

    def test_project_config_validate_rejects_legacy_provider_credentials_and_runtime_live(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Legacy Config Desk")
            path = root / "kairos.toml"
            path.write_text(
                path.read_text(encoding="utf-8") + "\n".join([
                    "",
                    "[providers.legacy]",
                    'api_key = "env:MASSIVE_API_KEY"',
                    "",
                    "[runtime.live]",
                    "enabled = true",
                    "",
                    "[credentials.legacy_binance]",
                    'api_key = "env:BINANCE_LIVE_API_KEY"',
                ]) + "\n",
                encoding="utf-8",
            )

            issues = KairosProjectConfig.load(path).validate()

            self.assertTrue(any("providers.legacy.api_key is not valid project config" in issue for issue in issues))
            self.assertTrue(any("[runtime.live] is not valid project config" in issue for issue in issues))
            self.assertTrue(any("legacy environment variable BINANCE_LIVE_API_KEY" in issue for issue in issues))

    def test_cli_config_and_doctor_use_local_project(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Cli Desk")
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
            env["KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY"] = "secret"

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
            self.assertEqual(
                payload["credentials"]["massive_marketdata_primary"]["api_key"],
                "env:KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY",
            )

            doctor = subprocess.run(
                [sys.executable, "-m", "kairospy", "--format", "json", "doctor"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            checks = json.loads(doctor.stdout)["checks"]
            self.assertIn({"name": "config", "status": "ok", "detail": "kairos.toml is structurally valid"}, checks)
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
            self.assertEqual(config.get("credentials.massive_marketdata_primary.api_key"), "env:MY_MASSIVE_KEY")
            self.assertEqual(config.get("credentials.binance_trading_live_spot.api_key"), "env:MY_BINANCE_KEY")
            self.assertEqual(config.get("credentials.binance_trading_live_spot.api_secret"), "env:MY_BINANCE_SECRET")

    def test_cli_human_output_uses_professional_status_tables(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Output Desk")
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
            env["KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY"] = "secret"

            doctor = subprocess.run(
                [sys.executable, "-m", "kairospy", "doctor"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn("Kairos Doctor", doctor.stdout)
            self.assertIn("config", doctor.stdout)
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
            self.assertIn("credentials.massive_marketdata_primary.api_key", show.stdout)

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
            self.assertEqual(config.get("credentials.binance_trading_testnet_spot.api_key"), "env:PIPE_BINANCE_KEY")
            self.assertEqual(config.get("credentials.binance_trading_testnet_spot.api_secret"), "env:PIPE_BINANCE_SECRET")

    def test_cli_data_acquire_reports_removed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Acquire Desk")
            env = dict(os.environ)
            env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")

            listed = subprocess.run(
                [sys.executable, "-m", "kairospy", "--format", "json", "data", "acquire", "--list-products"],
                cwd=root,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(listed.returncode, 2)
            payload = json.loads(listed.stdout)
            self.assertEqual(payload["status"], "removed")
            self.assertEqual(payload["operation"], "acquire")

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
            self.assertIn("removed", blocked.stderr + blocked.stdout)

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
            self.assertIn("Operation", human.stdout)

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
            self.assertIn("datasets", payload)
            self.assertNotIn("Kairos Data Catalog", machine.stdout)


if __name__ == "__main__":
    unittest.main()
