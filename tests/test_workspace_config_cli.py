from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from typer.testing import CliRunner

from kairospy.application.system.workspace import KairosWorkspace
import kairospy.application.system.facade.account as account_facade
import kairospy.surface.cli.commands.account as account_product
from kairospy.infrastructure.integrations.connectors.exchange.okx.market_data import _okx_config
from kairospy.surface.cli import app, execute_argv
from kairospy.surface.cli.commands.account import account_app
from kairospy.surface.cli.commands.launch import launch_app


def test_workspace_resolves_project_paths_and_local_accounts(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".kairos").mkdir()
    (tmp_path / ".kairos" / "kairos.toml").write_text(
        "\n".join(
            [
                "[paths]",
                'workspace_root = ".kairos"',
                'launch_root = ".kairos/launch-artifacts"',
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
    assert workspace.launch_root == tmp_path / ".kairos" / "launch-artifacts"
    assert workspace.accounts.get("local_live").credential_values["api_key"] == "abc"


def test_workspace_account_registry_exposes_declared_books(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "binance_main.toml").write_text(
        "\n".join(
            [
                "[account]",
                'id = "main"',
                'provider = "binance"',
                'environment = "live"',
                'venue = "binance"',
                "",
                "[books.spot]",
                'kind = "spot"',
                "",
                "[books.perp]",
                'kind = "usd_m_futures"',
                'alias = "hedge_perp"',
            ]
        ),
        encoding="utf-8",
    )

    workspace = KairosWorkspace.resolve()
    account = workspace.accounts.get("main")
    directory = workspace.accounts.directory()

    assert [book.kind for book in account.books] == ["usd_m_futures", "spot"]
    assert account.account_key == "binance.main"
    assert str(directory.require("hedge_perp").book) == "usd_m_futures"


def test_workspace_account_accepts_minimal_broker_account(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "binance_live.toml").write_text(
        "\n".join(
            [
                "[account]",
                'id = "binance_zhaoqian888666"',
                'broker = "binance"',
                'environment = "live"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    account = KairosWorkspace.resolve().accounts.get("binance_zhaoqian888666")

    assert account.provider == "binance"
    assert account.venue == "binance"
    assert [book.kind for book in account.books] == ["spot", "cross_margin", "isolated_margin", "usd_m_futures", "coin_m_futures", "funding"]


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
    assert payload["broker"] == "binance"
    assert payload["provider"] == "binance"
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
    assert "ID             BROKER   ENV    VENUE    MARKET  CCY   CASH  FEE    CREDENTIAL" in result.output
    assert "binance_paper  binance  paper  binance  spot    USDT  1000  0.001  -" in result.output


def test_account_cli_schema_uses_broker_language() -> None:
    result = CliRunner().invoke(account_app, ["schema", "binance"], catch_exceptions=False)
    schemas = CliRunner().invoke(account_app, ["schemas"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "Account Schema binance" in result.output
    assert "balance_books" in result.output
    assert "default_book" not in result.output
    assert "default_market" not in result.output
    assert schemas.exit_code == 0
    assert "Account Schemas" in schemas.output
    assert "binance" in schemas.output


def test_account_cli_list_shows_named_credentials(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "binance_live.toml").write_text(
        "\n".join(
            [
                "[account]",
                'broker = "binance"',
                'environment = "live"',
                "",
                "[credentials.readonly]",
                'ref = "binance_read"',
                "",
                "[credentials.trade]",
                'ref = "binance_trade"',
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(account_app, ["list"], catch_exceptions=False)
    show = CliRunner().invoke(account_app, ["show", "binance_live", "--format", "text"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "readonly:binance_read,trade:binance_trade" in result.output
    assert show.exit_code == 0
    assert "broker       binance" in show.output
    assert "readonly binance_read" in show.output
    assert "trade    binance_trade" in show.output


def test_account_cli_reads_balance_through_configured_account(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "binance_testnet.toml").write_text(
        "\n".join(
            [
                "[account]",
                'broker = "binance"',
                'environment = "testnet"',
                "",
                "[credentials.readonly]",
                'ref = "binance_read"',
            ]
        ),
        encoding="utf-8",
    )
    seen = []

    class FakeBroker:
        def fetch_balance(self, *, params=None):
            seen.append(dict(params or {}))
            return {
                "free": {"USDT": "100", "ZERO": "0"},
                "used": {"USDT": "0", "ZERO": "0"},
                "total": {"USDT": "100", "ZERO": "0"},
            }

    def fake_broker(exchange_name, driver_name, *, credential=None):
        assert credential == "binance_read"
        return FakeBroker()

    monkeypatch.setattr(account_facade, "broker", fake_broker)

    result = CliRunner().invoke(account_app, ["balance", "binance_testnet", "--book", "spot"], catch_exceptions=False)

    assert result.exit_code == 0
    assert seen == [{}]
    assert "Balances  binance_testnet  books=spot" in result.output
    assert "USDT" in result.output
    assert "ZERO" not in result.output
    assert "page 1/1  rows 1/1" in result.output
    assert "Querying balances for binance_testnet" in result.stderr
    assert "[1/1] spot done" in result.stderr


def test_account_cli_balance_defaults_to_all_books_and_paginates(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "binance_live.toml").write_text(
        "\n".join(
            [
                "[account]",
                'broker = "binance"',
                'environment = "live"',
                "",
                "[credentials.readonly]",
                'ref = "binance_read"',
            ]
        ),
        encoding="utf-8",
    )
    seen_params = []

    class FakeBroker:
        def fetch_balance(self, *, params=None):
            values = dict(params or {})
            seen_params.append(values)
            label = str(values.get("type") or "default").upper()
            return {"free": {label: "1"}, "used": {label: "0"}, "total": {label: "1"}}

    monkeypatch.setattr(account_facade, "broker", lambda exchange_name, driver_name, *, credential=None: FakeBroker())

    result = CliRunner().invoke(account_app, ["balance", "binance_live", "--page-size", "2", "--format", "json"], catch_exceptions=False)

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload["books"] == ["spot", "cross_margin", "isolated_margin", "usd_m_futures", "coin_m_futures", "funding"]
    assert len(seen_params) == 6
    assert seen_params[0] == {}
    assert seen_params[-1] == {"type": "funding"}
    assert payload["page"] == {"page": 1, "page_size": 2, "total_rows": 6, "total_pages": 3}
    assert len(payload["rows"]) == 2
    assert "Querying balances" not in result.output


def test_account_cli_balance_continues_when_a_book_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "binance_live.toml").write_text(
        "\n".join(
            [
                "[account]",
                'broker = "binance"',
                'environment = "live"',
                "",
                "[credentials.readonly]",
                'ref = "binance_read"',
            ]
        ),
        encoding="utf-8",
    )

    class FakeBroker:
        def fetch_balance(self, *, params=None):
            if dict(params or {}).get("type") == "funding":
                raise RuntimeError("funding permission denied")
            return {"free": {"USDT": "1"}, "used": {"USDT": "0"}, "total": {"USDT": "1"}}

    monkeypatch.setattr(account_facade, "broker", lambda exchange_name, driver_name, *, credential=None: FakeBroker())

    result = CliRunner().invoke(account_app, ["balance", "binance_live", "--book", "spot", "--book", "funding"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "USDT" in result.output
    assert "Balance Errors" in result.output
    assert "RuntimeError" in result.output
    assert "diag-" in result.output
    assert "funding permission denied" in result.output
    diagnostic_path = next((tmp_path / ".kairos" / "logs" / "cli").glob("*.jsonl"))
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8").splitlines()[-1])
    assert diagnostic["operation"] == "account.balance.fetch_book"
    assert diagnostic["context"]["book"] == "funding"
    assert diagnostic["context"]["params"] == {"type": "funding"}
    assert diagnostic["error_type"] == "RuntimeError"


def test_account_cli_balance_errors_include_diagnostics_in_json(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "binance_live.toml").write_text(
        "\n".join(
            [
                "[account]",
                'broker = "binance"',
                'environment = "live"',
            ]
        ),
        encoding="utf-8",
    )

    class FakeBroker:
        def fetch_balance(self, *, params=None):
            raise RuntimeError("permission denied")

    monkeypatch.setattr(account_facade, "broker", lambda exchange_name, driver_name, *, credential=None: FakeBroker())

    result = CliRunner().invoke(
        account_app,
        ["balance", "binance_live", "--book", "spot", "--params-json", '{"api_secret":"abc"}', "--format", "json"],
        catch_exceptions=False,
    )

    payload = json.loads(result.output)
    error = payload["errors"][0]
    assert result.exit_code == 0
    assert error["error_type"] == "RuntimeError"
    assert error["diagnostic_id"].startswith("diag-")
    assert error["duration_ms"] >= 0
    diagnostic = json.loads(Path(error["diagnostic_path"]).read_text(encoding="utf-8").splitlines()[-1])
    assert diagnostic["id"] == error["diagnostic_id"]
    assert diagnostic["context"]["account"] == "binance_live"
    assert diagnostic["context"]["params"]["api_secret"] == "<redacted>"


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


def test_account_cli_runtime_queries_use_launch_command_channel(monkeypatch) -> None:
    calls = []

    class FakeLaunches:
        def submit_command(self, **kwargs):
            calls.append(kwargs)
            return {
                "kind": kwargs["kind"],
                "command_id": "command-1",
                "response": {
                    "status": "accepted",
                    "result": {"current": {"cash": "1000"}},
                },
            }

    monkeypatch.setattr(account_product, "_RUNS", FakeLaunches())

    result = CliRunner().invoke(
        account_app,
        ["current", "--launch", "paper-main", "--account", "main", "--format", "json"],
        catch_exceptions=False,
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload["response"]["result"]["current"]["cash"] == "1000"
    assert calls == [
        {
            "target": "paper-main",
            "root": None,
            "launch_id": None,
            "mode": None,
            "kind": "account.current",
            "payload": {"account": "main"},
            "wait": True,
            "timeout_seconds": 5.0,
        }
    ]


def test_account_cli_trade_status_uses_system_command_channel(monkeypatch, tmp_path) -> None:
    calls = []

    class FakeLaunches:
        def system_command(self, **kwargs):
            calls.append(kwargs)
            return {
                "kind": kwargs["kind"],
                "response": {
                    "status": "accepted",
                    "result": {"accounts": [{"account": "main", "can_trade": True}]},
                },
            }

    monkeypatch.setattr(account_product, "_RUNS", FakeLaunches())

    result = CliRunner().invoke(
        account_app,
        ["trade-status", "main", "--root", str(tmp_path), "--no-wait", "--timeout", "1", "--format", "json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["kind"] == "account.trade-status"
    assert calls == [
        {
            "kind": "account.trade-status",
            "payload": {"account": "main"},
            "root": tmp_path,
            "launch_id": "kairos-system",
            "wait": False,
            "timeout_seconds": 1.0,
        }
    ]


def test_launch_index_registers_and_validates_named_run(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    config_dir = tmp_path / "configs" / "launches"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "backtest.toml"
    config_path.write_text(
        "\n".join(
            [
                "[launch]",
                'id = "bt"',
                'mode = "backtest"',
                'strategy = "examples.strategies.sma:strategy"',
            ]
        ),
        encoding="utf-8",
    )

    register = CliRunner().invoke(launch_app, ["targets", "add", "bt", str(config_path)], catch_exceptions=False)
    specs = CliRunner().invoke(launch_app, ["targets", "index", "--format", "json"], catch_exceptions=False)
    validate = CliRunner().invoke(launch_app, ["diagnose", "validate", "bt", "--format", "json"], catch_exceptions=False)

    assert register.exit_code == 0
    assert json.loads(specs.output)["launches"]["bt"]["config"] == "configs/launches/backtest.toml"
    assert json.loads(validate.output)["valid"] is True
    operations = (tmp_path / ".kairos" / "state" / "operations.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(operations[-1])["action"] == "launch.register"


def test_launch_index_register_infers_name_from_launch_config(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    config_dir = tmp_path / "configs" / "launches"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "backtest.toml"
    config_path.write_text(
        "\n".join(
            [
                "[launch]",
                'id = "bt-inferred"',
                'mode = "backtest"',
                'strategy = "examples.strategies.sma:strategy"',
            ]
        ),
        encoding="utf-8",
    )

    register = CliRunner().invoke(launch_app, ["targets", "add", str(config_path), "--format", "json"], catch_exceptions=False)
    specs = CliRunner().invoke(launch_app, ["targets", "index", "--format", "json"], catch_exceptions=False)

    assert register.exit_code == 0
    assert json.loads(register.output)["name"] == "bt-inferred"
    assert json.loads(specs.output)["launches"]["bt-inferred"]["config"] == "configs/launches/backtest.toml"


def test_workspace_manifest_cli_format_controls_launch_register_output(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    (tmp_path / ".kairos" / "kairos.toml").write_text("[project]\nname = \"test\"\n[cli]\nformat = \"text\"\n", encoding="utf-8")
    config_dir = tmp_path / "configs" / "launches"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "backtest.toml"
    config_path.write_text(
        "\n".join(
            [
                "[launch]",
                'id = "bt-text"',
                'mode = "backtest"',
                'strategy = "examples.strategies.sma:strategy"',
            ]
        ),
        encoding="utf-8",
    )

    register = CliRunner().invoke(app, ["launch", "targets", "add", str(config_path)], catch_exceptions=False)

    assert register.exit_code == 0
    assert register.output.startswith("Launch Registered\n")
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
    config_dir = tmp_path / "configs" / "launches"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "paper.toml"
    config_path.write_text(
        "\n".join(
            [
                "[launch]",
                'id = "paper-launch"',
                'mode = "paper"',
                'strategy = "examples.strategies.sma:strategy"',
                "",
                "[account]",
                'ref = "paper"',
            ]
        ),
        encoding="utf-8",
    )
    CliRunner().invoke(launch_app, ["targets", "add", "paper-launch", str(config_path)], catch_exceptions=False)

    explain = CliRunner().invoke(app, ["config", "explain", "--launch", "paper-launch"], catch_exceptions=False)
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
    manifest = tmp_path / "demo" / ".kairos" / "kairos.toml"
    assert manifest.exists()
    assert 'language = "en"' in manifest.read_text(encoding="utf-8")
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
    assert 'broker = "binance"' in text
    assert 'provider = "binance"' not in text
    assert 'venue = "binance"' not in text
    assert 'market = "spot"' not in text
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
    assert 'broker = "binance"' in text
    assert 'environment = "paper"' in text
    assert 'venue = "binance"' not in text
    assert 'market = "spot"' not in text
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
    credential_path = project / ".kairos" / "credentials" / "okx_live_spot.toml"
    text = path.read_text(encoding="utf-8")
    credential_text = credential_path.read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert 'broker = "okx"' in text
    assert 'market = "spot"' not in text
    assert "cash =" not in text
    assert "fee_rate =" not in text
    assert "[credentials.readonly]" in text
    assert 'ref = "okx_live_spot"' in text
    assert "[credential]" not in text
    assert 'broker = "okx"' in credential_text
    assert 'provider = "okx"' not in credential_text
    assert 'kind = "api_key_secret_passphrase"' in credential_text
    assert 'passphrase = "phrase"' in credential_text

    show = CliRunner().invoke(account_app, ["show", "okx_live_spot", "--format", "json"], catch_exceptions=False)
    payload = json.loads(show.output)
    assert payload["credential"] is None
    assert payload["broker"] == "okx"
    assert payload["credentials"][0]["name"] == "readonly"
    assert payload["credentials"][0]["ref"] == "okx_live_spot"


def test_account_cli_create_can_write_key_to_explicit_credential_id(tmp_path, monkeypatch) -> None:
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
            "--credential",
            "okx_trade",
            "--api-key",
            "key",
            "--api-secret",
            "secret",
            "--passphrase",
            "phrase",
        ],
        catch_exceptions=False,
    )

    account_text = (project / ".kairos" / "accounts" / "okx_live_spot.toml").read_text(encoding="utf-8")
    credential_text = (project / ".kairos" / "credentials" / "okx_trade.toml").read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert "[credentials.readonly]" in account_text
    assert 'ref = "okx_trade"' in account_text
    assert 'broker = "okx"' in credential_text
    assert 'api_key = "key"' in credential_text
    assert 'api_secret = "secret"' in credential_text


def test_account_cli_adds_named_api_credentials(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)

    create = CliRunner().invoke(
        account_app,
        [
            "create",
            "okx_live_multi",
            "--provider",
            "okx",
            "--environment",
            "live",
        ],
        catch_exceptions=False,
    )
    read = CliRunner().invoke(
        account_app,
        ["credential-add", "okx_live_multi", "readonly", "--ref", "okx_read", "--no-check"],
        catch_exceptions=False,
    )
    trade = CliRunner().invoke(
        account_app,
        [
            "credential-add",
            "okx_live_multi",
            "trade",
            "--ref",
            "okx_trade",
            "--no-check",
        ],
        catch_exceptions=False,
    )

    path = project / ".kairos" / "accounts" / "okx_live_multi.toml"
    text = path.read_text(encoding="utf-8")
    account = KairosWorkspace.resolve().accounts.get("okx_live_multi")
    assert create.exit_code == 0
    assert read.exit_code == 0
    assert trade.exit_code == 0
    assert "[credentials.readonly]" in text
    assert 'ref = "okx_read"' in text
    assert "[credentials.trade]" in text
    assert account.credentials[0].name == "readonly"
    assert account.credentials[1].role == "trade"


def test_credential_cli_creates_local_secret_file_and_redacts_show(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)

    create = CliRunner().invoke(
        app,
        [
            "credential",
            "create",
            "okx_trade",
            "--provider",
            "okx",
            "--api-key",
            "key",
            "--api-secret",
            "secret",
            "--passphrase",
            "phrase",
        ],
        catch_exceptions=False,
    )
    show = CliRunner().invoke(app, ["credential", "show", "okx_trade", "--format", "json"], catch_exceptions=False)
    listed = CliRunner().invoke(app, ["credential", "list", "--format", "json"], catch_exceptions=False)

    path = project / ".kairos" / "credentials" / "okx_trade.toml"
    text = path.read_text(encoding="utf-8")
    payload = json.loads(show.output)
    assert create.exit_code == 0
    assert path.exists()
    assert 'broker = "okx"' in text
    assert 'provider = "okx"' not in text
    assert 'api_key = "key"' in text
    assert payload["broker"] == "okx"
    assert payload["provider"] == "okx"
    assert payload["values"]["api_key"] == "<redacted>"
    assert payload["values"]["api_secret"] == "<redacted>"
    listed_payload = json.loads(listed.output)
    assert listed_payload["count"] == 1
    assert listed_payload["credentials"][0]["broker"] == "okx"
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_credential_file_is_resolved_by_okx_broker_config(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)
    CliRunner().invoke(
        app,
        [
            "credential",
            "create",
            "okx_trade",
            "--provider",
            "okx",
            "--api-key",
            "key",
            "--api-secret",
            "secret",
            "--passphrase",
            "phrase",
        ],
        catch_exceptions=False,
    )
    monkeypatch.setenv("OKX_TRADE_API_KEY", "env-key")
    monkeypatch.setenv("OKX_TRADE_SECRET", "env-secret")
    monkeypatch.setenv("OKX_TRADE_PASSWORD", "env-phrase")

    config = _okx_config("okx_trade")

    assert config["apiKey"] == "key"
    assert config["secret"] == "secret"
    assert config["password"] == "phrase"


def test_account_cli_credential_add_checks_declared_permissions(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)
    CliRunner().invoke(account_app, ["create", "okx_live_check", "--provider", "okx", "--environment", "live"], catch_exceptions=False)
    _install_credential_brokers(monkeypatch, {"okx_read": {"read_private": True, "account_id": "acct-1"}})

    result = CliRunner().invoke(
        account_app,
        ["credential-add", "okx_live_check", "trade", "--ref", "okx_read"],
    )

    assert result.exit_code != 0
    assert "is not a trade credential" in result.output


def test_account_cli_credential_add_checks_same_account_identity(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)
    CliRunner().invoke(account_app, ["create", "okx_live_same", "--provider", "okx", "--environment", "live"], catch_exceptions=False)
    _install_credential_brokers(
        monkeypatch,
        {
            "okx_read": {"read_private": True, "account_id": "acct-1"},
            "okx_trade": {"read_private": True, "trade": True, "account_id": "acct-2"},
        },
    )
    read = CliRunner().invoke(
        account_app,
        ["credential-add", "okx_live_same", "readonly", "--ref", "okx_read"],
        catch_exceptions=False,
    )
    trade = CliRunner().invoke(
        account_app,
        ["credential-add", "okx_live_same", "trade", "--ref", "okx_trade"],
    )

    assert read.exit_code == 0
    assert trade.exit_code != 0
    assert "belongs to a different account" in trade.output


def test_account_cli_rejects_env_prefixed_credential_refs(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)
    CliRunner().invoke(
        account_app,
        ["create", "okx_live_env", "--provider", "okx", "--environment", "live"],
        catch_exceptions=False,
    )

    result = CliRunner().invoke(
        account_app,
        ["credential-add", "okx_live_env", "trade", "--ref", "env:okx_trade"],
    )

    assert result.exit_code != 0
    assert "not an env: reference" in result.output


def test_account_cli_modifies_account_fields_without_touching_credentials(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)
    CliRunner().invoke(
        account_app,
        ["create", "binance_live", "--provider", "binance", "--environment", "live", "--credential", "binance_default"],
        catch_exceptions=False,
    )
    CliRunner().invoke(
        account_app,
        ["credential-add", "binance_live", "trade", "--ref", "binance_trade_readwrite", "--no-check"],
        catch_exceptions=False,
    )

    result = CliRunner().invoke(
        account_app,
        [
            "modify",
            "binance_live",
            "--credential",
            "binance_trade",
            "--field",
            "index=2",
        ],
        catch_exceptions=False,
    )

    account = KairosWorkspace.resolve().accounts.get("binance_live")
    text = (project / ".kairos" / "accounts" / "binance_live.toml").read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert account.credential == "binance_trade"
    assert account.values["index"] == "2"
    assert {credential.name: credential.ref for credential in account.credentials} == {"readonly": "binance_default", "trade": "binance_trade_readwrite"}
    assert "[credentials.readonly]" in text


def test_account_cli_modify_accepts_broker_alias(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)
    CliRunner().invoke(
        account_app,
        ["create", "broker_alias", "--broker", "binance", "--environment", "live"],
        catch_exceptions=False,
    )

    result = CliRunner().invoke(account_app, ["modify", "broker_alias", "--broker", "okx"], catch_exceptions=False)

    account = KairosWorkspace.resolve().accounts.get("broker_alias")
    assert result.exit_code == 0
    assert account.provider == "okx"


def test_account_cli_modify_rejects_live_simulated_fields(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)
    CliRunner().invoke(
        account_app,
        ["create", "binance_live", "--provider", "binance", "--environment", "live"],
        catch_exceptions=False,
    )

    result = CliRunner().invoke(account_app, ["modify", "binance_live", "--currency", "USDT"])

    assert result.exit_code != 0
    assert "simulated accounts" in result.output


def test_account_cli_modify_allows_paper_simulated_fields(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)
    CliRunner().invoke(
        account_app,
        ["create", "binance_paper", "--provider", "binance", "--environment", "paper"],
        catch_exceptions=False,
    )

    result = CliRunner().invoke(
        account_app,
        ["modify", "binance_paper", "--currency", "USDT", "--cash", "5000", "--fee-rate", "0.0005"],
        catch_exceptions=False,
    )

    account = KairosWorkspace.resolve().accounts.get("binance_paper")
    assert result.exit_code == 0
    assert account.values["currency"] == "USDT"
    assert account.values["cash"] == "5000"
    assert account.values["fee_rate"] == "0.0005"


def test_account_cli_modify_rejects_env_prefixed_credential(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)
    CliRunner().invoke(
        account_app,
        ["create", "binance_live", "--provider", "binance", "--environment", "live"],
        catch_exceptions=False,
    )

    result = CliRunner().invoke(account_app, ["modify", "binance_live", "--credential", "env:binance_trade"])

    assert result.exit_code != 0
    assert "not an env: reference" in result.output


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
    credential_path = project / ".kairos" / "credentials" / "hl_perp.toml"
    text = path.read_text(encoding="utf-8")
    credential_text = credential_path.read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert 'broker = "hyperliquid"' in text
    assert 'market = "swap"' not in text
    assert "[credentials.readonly]" in text
    assert 'ref = "hl_perp"' in text
    assert 'broker = "hyperliquid"' in credential_text
    assert 'kind = "wallet_private_key"' in credential_text
    assert 'wallet_address = "0xabc"' in credential_text
    assert 'private_key = "0xdef"' in credential_text

    doctor = CliRunner().invoke(account_app, ["doctor", "hl_perp", "--format", "json"], catch_exceptions=False)
    assert json.loads(doctor.output)["valid"] is True


def test_account_cli_interactive_create_guides_required_fields(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)

    result = CliRunner().invoke(
        account_app,
        ["create", "--interactive"],
        input="\n".join(["binance_paper", "binance", "paper", "", "", "", "y"]) + "\n",
        catch_exceptions=False,
    )

    path = project / ".kairos" / "accounts" / "binance_paper.toml"
    text = path.read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert "Account create wizard" in result.output
    assert f"created account: {path}" in result.output
    assert 'broker = "binance"' in text
    assert 'environment = "paper"' in text
    assert 'market = "spot"' not in text
    assert 'cash = "100000"' in text


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
    payload = json.loads(result.output)
    assert payload["root"] == str(tmp_path)
    assert payload["timezone"] == "UTC"
    assert payload["language"] == "en"


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


def _install_credential_brokers(monkeypatch, profiles: dict[str, dict[str, object]]) -> None:
    monkeypatch.setattr(account_product._ACCOUNTS, "_credential_broker", lambda account, ref: _FakeCredentialBroker(profiles[ref]))


class _FakeCredentialBroker:
    def __init__(self, profile: dict[str, object]) -> None:
        self.profile = profile

    def inspect_credential(self) -> dict[str, object]:
        return dict(self.profile)


def _write_workspace_manifest(root) -> None:
    kairos = root / ".kairos"
    kairos.mkdir(parents=True, exist_ok=True)
    (kairos / "kairos.toml").write_text("[project]\nname = \"test\"\n", encoding="utf-8")
