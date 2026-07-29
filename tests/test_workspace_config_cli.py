from __future__ import annotations

import json
from io import StringIO

from typer.testing import CliRunner

from kairospy.application.system.workspace import KairosWorkspace
import kairospy.surface.cli.commands.account as account_product
from kairospy.surface.cli import app, execute_argv
from kairospy.surface.cli.commands.account import account_app
from kairospy.surface.cli.commands.run import run_app


def test_workspace_resolves_project_paths_and_local_accounts(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".kairos").mkdir()
    (tmp_path / ".kairos" / "kairos.toml").write_text(
        "\n".join(
            [
                "[paths]",
                'workspace_root = ".kairos"',
                'run_root = ".kairos/run-artifacts"',
            ]
        ),
        encoding="utf-8",
    )
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "local_live.toml").write_text(
        "\n".join(
            [
                "[account]",
                'id = "local_live"',
                'provider = "binance"',
                'environment = "live"',
                'venue = "binance"',
                'market = "spot"',
                "",
                "[credential]",
                'kind = "api_key_secret"',
                'api_key = "abc"',
                'api_secret = "def"',
            ]
        ),
        encoding="utf-8",
    )

    workspace = KairosWorkspace.resolve()

    assert workspace.root == tmp_path
    assert workspace.run_root == tmp_path / ".kairos" / "run-artifacts"
    assert workspace.accounts.get("local_live").credential_values["api_key"] == "abc"


def test_account_cli_lists_and_redacts_local_account(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "binance_live.toml").write_text(
        "\n".join(
            [
                "[account]",
                'provider = "binance"',
                'environment = "live"',
                "",
                "[credential]",
                'api_key = "abc"',
                'api_secret = "def"',
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(account_app, ["show", "binance_live"], catch_exceptions=False)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["credential_values"]["api_key"] == "<redacted>"
    assert payload["credential_values"]["api_secret"] == "<redacted>"


def test_account_cli_lists_accounts_as_readable_table(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "binance_paper.toml").write_text(
        "\n".join(
            [
                "[account]",
                'provider = "binance"',
                'environment = "paper"',
                'venue = "binance"',
                'market = "spot"',
                'currency = "USDT"',
                'cash = "1000"',
                'fee_rate = "0.001"',
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(account_app, ["list"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "ID             PROVIDER  ENV    VENUE    MARKET  CCY   CASH  FEE    CREDENTIAL" in result.output
    assert "binance_paper  binance   paper  binance  spot    USDT  1000  0.001  -" in result.output


def test_account_cli_reads_balance_through_configured_account(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "binance_testnet.toml").write_text(
        "\n".join(
            [
                "[account]",
                'provider = "binance"',
                'environment = "testnet"',
                'venue = "binance"',
            ]
        ),
        encoding="utf-8",
    )

    class FakeBroker:
        def fetch_balance(self, *, params=None):
            return {"total": {"USDT": "100"}}

    monkeypatch.setattr(account_product._ACCOUNTS, "_broker", lambda account: FakeBroker())

    result = CliRunner().invoke(account_app, ["balance", "binance_testnet"], catch_exceptions=False)

    assert result.exit_code == 0
    assert json.loads(result.output)["balance"]["total"]["USDT"] == "100"


def test_account_cli_snapshot_and_doctor_use_local_account(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "binance_testnet.toml").write_text(
        "\n".join(
            [
                "[account]",
                'provider = "binance"',
                'environment = "testnet"',
                'venue = "binance"',
            ]
        ),
        encoding="utf-8",
    )

    class FakeBroker:
        def fetch_balance(self, *, params=None):
            return {"total": {"USDT": "100"}}

        def fetch_open_orders(self, symbol=None, limit=None, params=None):
            return ({"id": "order-1", "symbol": symbol or "BTC/USDT"},)

    monkeypatch.setattr(account_product._ACCOUNTS, "_broker", lambda account: FakeBroker())

    snapshot = CliRunner().invoke(account_app, ["snapshot", "binance_testnet"], catch_exceptions=False)
    doctor = CliRunner().invoke(account_app, ["doctor", "binance_testnet", "--format", "json"], catch_exceptions=False)

    assert snapshot.exit_code == 0
    assert json.loads(snapshot.output)["open_orders"][0]["id"] == "order-1"
    assert (tmp_path / ".kairos" / "accounts" / "journals" / "binance_testnet.jsonl").exists()
    assert doctor.exit_code == 0
    assert json.loads(doctor.output)["valid"] is True


def test_run_index_registers_and_validates_named_run(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    config_dir = tmp_path / "configs" / "runs"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "backtest.toml"
    config_path.write_text(
        "\n".join(
            [
                "[run]",
                'id = "bt"',
                'mode = "backtest"',
                'strategy = "examples.strategies.sma:strategy"',
            ]
        ),
        encoding="utf-8",
    )

    register = CliRunner().invoke(run_app, ["register", "bt", str(config_path)], catch_exceptions=False)
    specs = CliRunner().invoke(run_app, ["specs", "--format", "json"], catch_exceptions=False)
    validate = CliRunner().invoke(run_app, ["validate", "bt", "--format", "json"], catch_exceptions=False)

    assert register.exit_code == 0
    assert json.loads(specs.output)["runs"]["bt"]["config"] == "configs/runs/backtest.toml"
    assert json.loads(validate.output)["valid"] is True
    operations = (tmp_path / ".kairos" / "state" / "operations.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(operations[-1])["action"] == "run.register"


def test_run_index_register_infers_name_from_run_config(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    config_dir = tmp_path / "configs" / "runs"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "backtest.toml"
    config_path.write_text(
        "\n".join(
            [
                "[run]",
                'id = "bt-inferred"',
                'mode = "backtest"',
                'strategy = "examples.strategies.sma:strategy"',
            ]
        ),
        encoding="utf-8",
    )

    register = CliRunner().invoke(run_app, ["register", str(config_path), "--format", "json"], catch_exceptions=False)
    specs = CliRunner().invoke(run_app, ["specs", "--format", "json"], catch_exceptions=False)

    assert register.exit_code == 0
    assert json.loads(register.output)["name"] == "bt-inferred"
    assert json.loads(specs.output)["runs"]["bt-inferred"]["config"] == "configs/runs/backtest.toml"


def test_workspace_manifest_cli_format_controls_run_register_output(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    (tmp_path / ".kairos" / "kairos.toml").write_text("[project]\nname = \"test\"\n[cli]\nformat = \"text\"\n", encoding="utf-8")
    config_dir = tmp_path / "configs" / "runs"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "backtest.toml"
    config_path.write_text(
        "\n".join(
            [
                "[run]",
                'id = "bt-text"',
                'mode = "backtest"',
                'strategy = "examples.strategies.sma:strategy"',
            ]
        ),
        encoding="utf-8",
    )

    register = CliRunner().invoke(app, ["run", "register", str(config_path)], catch_exceptions=False)

    assert register.exit_code == 0
    assert register.output.startswith("Run Registered\n")
    assert "  name    bt-text\n" in register.output


def test_config_explain_and_profile_commands(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "paper.toml").write_text(
        "\n".join(["[account]", 'provider = "binance"', 'environment = "paper"', 'venue = "binance"']),
        encoding="utf-8",
    )
    profile_template = tmp_path / "profile-template.toml"
    profile_template.write_text("[cli]\nformat = \"json\"\n", encoding="utf-8")
    config_dir = tmp_path / "configs" / "runs"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "paper.toml"
    config_path.write_text(
        "\n".join(
            [
                "[run]",
                'id = "paper-run"',
                'mode = "paper"',
                'strategy = "examples.strategies.sma:strategy"',
                "",
                "[account]",
                'ref = "paper"',
            ]
        ),
        encoding="utf-8",
    )
    CliRunner().invoke(run_app, ["register", "paper-run", str(config_path)], catch_exceptions=False)

    explain = CliRunner().invoke(app, ["config", "explain", "--run", "paper-run"], catch_exceptions=False)
    create_profile = CliRunner().invoke(app, ["config", "profile", "create", "local", "--from", str(profile_template)], catch_exceptions=False)
    use_profile = CliRunner().invoke(app, ["config", "profile", "use", "local"], catch_exceptions=False)
    profiles = CliRunner().invoke(app, ["config", "profile", "list", "--format", "json"], catch_exceptions=False)

    assert explain.exit_code == 0
    assert json.loads(explain.output)["sources"]["account"].endswith(".kairos/accounts/paper.toml")
    assert create_profile.exit_code == 0
    assert use_profile.exit_code == 0
    assert json.loads(profiles.output)["selected"] == "local"


def test_main_cli_exposes_config_and_account_products() -> None:
    result = CliRunner().invoke(app, ["--help"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "config" in result.output
    assert "account" in result.output
    assert "broker" not in result.output


def test_main_cli_initializes_local_project(tmp_path) -> None:
    result = CliRunner().invoke(app, ["project", "init", str(tmp_path / "demo")], catch_exceptions=False)

    assert result.exit_code == 0
    assert (tmp_path / "demo" / ".kairos" / "kairos.toml").exists()
    assert (tmp_path / "demo" / ".kairos" / "accounts").is_dir()
    operation = json.loads((tmp_path / "demo" / ".kairos" / "state" / "operations.jsonl").read_text(encoding="utf-8"))
    assert operation["action"] == "project.init"


def test_account_cli_creates_local_account_file(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)

    result = CliRunner().invoke(
        account_app,
        [
            "create",
            "binance_live_spot",
            "--provider",
            "binance",
            "--environment",
            "live",
            "--market",
            "spot",
            "--currency",
            "USDT",
            "--fee-rate",
            "0.001",
            "--credential-kind",
            "api_key_secret",
        ],
        catch_exceptions=False,
    )

    path = project / ".kairos" / "accounts" / "binance_live_spot.toml"
    assert result.exit_code == 0
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert 'provider = "binance"' in text
    assert 'fee_rate = "0.001"' in text
    operations = (project / ".kairos" / "state" / "operations.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(operations[-1])["action"] == "account.create"


def test_account_cli_creates_paper_account_with_simulated_fields(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)

    result = CliRunner().invoke(
        account_app,
        [
            "create",
            "binance_paper_spot",
            "--provider",
            "binance",
            "--environment",
            "paper",
            "--currency",
            "USDT",
            "--cash",
            "5000",
            "--fee-rate",
            "0.001",
        ],
        catch_exceptions=False,
    )

    path = project / ".kairos" / "accounts" / "binance_paper_spot.toml"
    text = path.read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert 'provider = "binance"' in text
    assert 'environment = "paper"' in text
    assert 'venue = "binance"' in text
    assert 'market = "spot"' in text
    assert 'cash = "5000"' in text
    assert 'fee_rate = "0.001"' in text
    assert "[credential]" not in text


def test_account_cli_creates_provider_specific_okx_fields(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)

    result = CliRunner().invoke(
        account_app,
        [
            "create",
            "okx_live_spot",
            "--provider",
            "okx",
            "--environment",
            "live",
            "--api-key",
            "key",
            "--api-secret",
            "secret",
            "--passphrase",
            "phrase",
        ],
        catch_exceptions=False,
    )

    path = project / ".kairos" / "accounts" / "okx_live_spot.toml"
    text = path.read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert 'provider = "okx"' in text
    assert 'market = "spot"' in text
    assert "cash =" not in text
    assert "fee_rate =" not in text
    assert 'kind = "api_key_secret_passphrase"' in text
    assert 'passphrase = "phrase"' in text

    show = CliRunner().invoke(account_app, ["show", "okx_live_spot", "--format", "json"], catch_exceptions=False)
    assert json.loads(show.output)["credential_values"]["passphrase"] == "<redacted>"


def test_account_cli_creates_provider_specific_hyperliquid_fields(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)

    result = CliRunner().invoke(
        account_app,
        [
            "create",
            "hl_perp",
            "--provider",
            "hyperliquid",
            "--environment",
            "live",
            "--wallet-address",
            "0xabc",
            "--private-key",
            "0xdef",
        ],
        catch_exceptions=False,
    )

    path = project / ".kairos" / "accounts" / "hl_perp.toml"
    text = path.read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert 'provider = "hyperliquid"' in text
    assert 'market = "swap"' in text
    assert 'kind = "wallet_private_key"' in text
    assert 'wallet_address = "0xabc"' in text
    assert 'private_key = "0xdef"' in text

    doctor = CliRunner().invoke(account_app, ["doctor", "hl_perp", "--format", "json"], catch_exceptions=False)
    assert json.loads(doctor.output)["valid"] is True


def test_account_cli_deletes_local_account_config(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)
    CliRunner().invoke(
        account_app,
        ["create", "binance_paper", "--provider", "binance", "--environment", "paper"],
        catch_exceptions=False,
    )

    result = CliRunner().invoke(account_app, ["delete", "binance_paper"], catch_exceptions=False)

    path = project / ".kairos" / "accounts" / "binance_paper.toml"
    assert result.exit_code == 0
    assert not path.exists()
    operations = (project / ".kairos" / "state" / "operations.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(operations[-1])["action"] == "account.delete"


def test_config_operations_reads_operation_journal(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)

    result = CliRunner().invoke(app, ["config", "operations", "--format", "json"], catch_exceptions=False)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["operations"][0]["action"] == "project.init"


def test_root_output_option_controls_command_output(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)

    result = CliRunner().invoke(app, ["--output", "json", "project", "status"], catch_exceptions=False)

    assert result.exit_code == 0
    assert json.loads(result.output)["root"] == str(tmp_path)


def test_command_format_option_overrides_root_output(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)

    result = CliRunner().invoke(app, ["--output", "json", "project", "status", "--format", "text"], catch_exceptions=False)

    assert result.exit_code == 0
    assert result.output.startswith("Project Status\n")


def test_selected_profile_controls_default_output_format(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    profile_root = tmp_path / ".kairos" / "profiles"
    profile_root.mkdir(parents=True)
    (profile_root / "local.toml").write_text("[cli]\nformat = \"json\"\n", encoding="utf-8")
    CliRunner().invoke(app, ["config", "profile", "use", "local"], catch_exceptions=False)

    result = CliRunner().invoke(app, ["project", "status"], catch_exceptions=False)

    assert result.exit_code == 0
    assert json.loads(result.output)["root"] == str(tmp_path)


def test_explicit_profile_overrides_selected_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    profile_root = tmp_path / ".kairos" / "profiles"
    profile_root.mkdir(parents=True)
    (profile_root / "json.toml").write_text("[cli]\nformat = \"json\"\n", encoding="utf-8")
    (profile_root / "text.toml").write_text("[cli]\nformat = \"text\"\n", encoding="utf-8")
    CliRunner().invoke(app, ["config", "profile", "use", "text"], catch_exceptions=False)

    result = CliRunner().invoke(app, ["--profile", "json", "project", "status"], catch_exceptions=False)

    assert result.exit_code == 0
    assert json.loads(result.output)["root"] == str(tmp_path)


def test_cwd_option_resolves_project_from_outside_workspace(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    _write_workspace_manifest(project)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)

    result = CliRunner().invoke(app, ["-C", str(project), "config", "paths", "--format", "json"], catch_exceptions=False)

    assert result.exit_code == 0
    assert json.loads(result.output)["root"] == str(project)


def test_cli_reports_init_hint_when_project_is_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    output = StringIO()

    exit_code = execute_argv(["config", "paths"], output)

    assert exit_code == 2
    assert "No Kairos project found from" in output.getvalue()
    assert "kairospy project init" in output.getvalue()


def _write_workspace_manifest(root) -> None:
    kairos = root / ".kairos"
    kairos.mkdir(parents=True, exist_ok=True)
    (kairos / "kairos.toml").write_text("[project]\nname = \"test\"\n", encoding="utf-8")
