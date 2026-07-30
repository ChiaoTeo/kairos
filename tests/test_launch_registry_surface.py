from __future__ import annotations

from io import StringIO
import json
from pathlib import Path

from typer.testing import CliRunner

import kairospy.surface.cli.commands.launch as launch_commands
import kairospy.surface.cli.commands.system as system_commands
from kairospy.surface.cli.app import execute_argv
from kairospy.surface.cli import app
from kairospy.surface.interactive.shell import AppSession
from kairospy.surface.cli.commands.launch import launch_app
from kairospy.surface.interactive.session import SurfaceContext


class _ProjectFacade:
    def __init__(self, root):
        self.root = root

    def surface_snapshot(self, *, stale_after_seconds: float = 5.0):
        _ = stale_after_seconds
        return {
            "project_name": self.root.name,
            "root": self.root,
            "data_root": self.root / ".kairos" / "data",
            "reference_root": self.root / ".kairos" / "reference",
            "launches": (),
        }


def _surface_context(root) -> SurfaceContext:
    return SurfaceContext(project_facade=_ProjectFacade(root))


def test_launch_list_reads_registered_launchs(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    config_path = _write_launch_config(tmp_path)

    register = CliRunner().invoke(launch_app, ["targets", "add", "btc-sma", str(config_path)], catch_exceptions=False)
    result = CliRunner().invoke(launch_app, ["targets", "list", "--format", "json"], catch_exceptions=False)

    assert register.exit_code == 0
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["count"] == 1
    assert payload["launches"][0]["name"] == "btc-sma"
    assert payload["launches"][0]["launch_id"] == "bt-1"
    assert payload["launches"][0]["mode"] == "backtest"
    assert payload["launches"][0]["strategy"] == "strategy_mod:ConfiguredStrategy"

    text = CliRunner().invoke(launch_app, ["targets", "list", "--format", "text"], catch_exceptions=False)

    assert "name     mode      launch_id" in text.output
    assert "btc-sma  backtest  bt-1" in text.output


def test_launch_instance_list_reads_rewritten_runtime_artifact_registry(tmp_path, monkeypatch) -> None:
    directory = tmp_path / "backtest" / "bt-1"
    directory.mkdir(parents=True)
    (directory / "summary.json").write_text(
        json.dumps({"launch_id": "bt-1", "mode": "backtest", "event_count": 2}) + "\n",
        encoding="utf-8",
    )
    (directory / "launch.log").write_text("strategy output\n", encoding="utf-8")

    result = CliRunner().invoke(launch_app, ["observe", "instances", "--root", str(tmp_path), "--format", "json"], catch_exceptions=False)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["count"] == 1
    assert payload["launches"][0]["launch_id"] == "bt-1"
    assert payload["launches"][0]["mode"] == "backtest"
    assert payload["launches"][0]["log_file"] == str(directory / "launch.log")

    filtered = CliRunner().invoke(
        launch_app,
        ["observe", "instances", "--root", str(tmp_path), "--launch-id", "bt-1", "--format", "json"],
        catch_exceptions=False,
    )

    assert json.loads(filtered.output)["count"] == 1

    positional = CliRunner().invoke(
        launch_app,
        ["observe", "instances", "bt-1", "--root", str(tmp_path), "--format", "json"],
        catch_exceptions=False,
    )

    assert json.loads(positional.output)["count"] == 1

    text = CliRunner().invoke(launch_app, ["observe", "instances", "--root", str(tmp_path), "--format", "text"], catch_exceptions=False)

    assert "mode      launch_id  status" in text.output
    assert "backtest  bt-1" in text.output
    assert str(directory / "launch.log") not in text.output

    default_text = CliRunner().invoke(launch_app, ["observe", "instances", "--root", str(tmp_path)], catch_exceptions=False)

    assert default_text.output.startswith("Launches\n")
    assert "backtest  bt-1" in default_text.output

    monkeypatch.chdir(tmp_path)
    details = CliRunner().invoke(
        launch_app,
        ["observe", "instances", "--root", str(tmp_path), "--details", "--format", "text"],
        catch_exceptions=False,
    )

    assert "directory" in details.output
    assert "backtest/bt-1/launch.log" in details.output
    assert str(directory / "launch.log") not in details.output


def test_launch_daemon_status_uses_artifact_registry(tmp_path) -> None:
    directory = tmp_path / "backtest" / "bt-1"
    directory.mkdir(parents=True)
    (directory / "summary.json").write_text(
        json.dumps({"launch_id": "bt-1", "mode": "backtest", "event_count": 2}) + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        launch_app,
        ["daemon", "status", "--root", str(tmp_path), "--launch-id", "bt-1", "--format", "json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["launches"][0]["launch_id"] == "bt-1"


def test_launch_system_trade_commands_wrap_system_command(monkeypatch, tmp_path) -> None:
    fake = _FakeLaunchFacade()
    monkeypatch.setattr(launch_commands, "_RUNS", fake)

    status = CliRunner().invoke(
        launch_app,
        ["system", "trade-status", "--root", str(tmp_path), "--no-wait", "--format", "json"],
        catch_exceptions=False,
    )
    acquire = CliRunner().invoke(
        launch_app,
        ["system", "trade-acquire", "main", "--root", str(tmp_path), "--timeout", "1", "--format", "json"],
        catch_exceptions=False,
    )
    release = CliRunner().invoke(
        launch_app,
        ["system", "trade-release", "binance.main", "--root", str(tmp_path), "--format", "json"],
        catch_exceptions=False,
    )

    assert status.exit_code == 0
    assert acquire.exit_code == 0
    assert release.exit_code == 0
    assert [call["kind"] for call in fake.calls] == ["account.trade-status", "account.trade-acquire", "account.trade-release"]
    assert fake.calls[0]["payload"] == {}
    assert fake.calls[0]["wait"] is False
    assert fake.calls[1]["payload"] == {"account": "main"}
    assert fake.calls[1]["timeout_seconds"] == 1.0
    assert fake.calls[2]["payload"] == {"account": "binance.main"}
    assert json.loads(acquire.output)["kind"] == "account.trade-acquire"


def test_top_level_system_account_trade_commands_wrap_system_command(monkeypatch, tmp_path) -> None:
    fake = _FakeLaunchFacade()
    monkeypatch.setattr(system_commands, "_RUNS", fake)

    status = CliRunner().invoke(
        app,
        ["system", "account", "trade-status", "main", "--root", str(tmp_path), "--no-wait", "--format", "json"],
        catch_exceptions=False,
    )
    acquire = CliRunner().invoke(
        app,
        ["system", "account", "trade-acquire", "main", "--root", str(tmp_path), "--format", "json"],
        catch_exceptions=False,
    )
    release = CliRunner().invoke(
        app,
        ["system", "account", "trade-release", "main", "--root", str(tmp_path), "--timeout", "1", "--format", "json"],
        catch_exceptions=False,
    )

    assert status.exit_code == 0
    assert acquire.exit_code == 0
    assert release.exit_code == 0
    assert [call["kind"] for call in fake.calls] == ["account.trade-status", "account.trade-acquire", "account.trade-release"]
    assert fake.calls[0]["payload"] == {"account": "main"}
    assert fake.calls[0]["wait"] is False
    assert fake.calls[2]["timeout_seconds"] == 1.0
    assert json.loads(status.output)["kind"] == "account.trade-status"


class _FakeLaunchFacade:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def system_command(
        self,
        *,
        kind: str,
        payload,
        root,
        launch_id: str,
        wait: bool,
        timeout_seconds: float,
    ) -> dict[str, object]:
        call = {
            "kind": kind,
            "payload": dict(payload or {}),
            "root": str(root) if root is not None else None,
            "launch_id": launch_id,
            "wait": wait,
            "timeout_seconds": timeout_seconds,
        }
        self.calls.append(call)
        return dict(call) | {"command_file": "commands/1.json", "response_file": "responses/1.json"}


def _write_workspace_manifest(root) -> None:
    (root / ".kairos").mkdir(parents=True, exist_ok=True)
    (root / ".kairos" / "kairos.toml").write_text("[project]\nname = \"test\"\n", encoding="utf-8")


def _write_launch_config(root) -> Path:
    config_path = root / "launch.toml"
    config_path.write_text(
        "\n".join(
            [
                "[launch]",
                'id = "bt-1"',
                'mode = "backtest"',
                'strategy = "strategy_mod:ConfiguredStrategy"',
                "",
                "[account]",
                "cash = 1000",
                'currency = "USDT"',
                "",
                "[backtest]",
                'storage_format = "jsonl"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


def test_execute_argv_returns_usage_errors_without_raising() -> None:
    output = StringIO()

    exit_code = execute_argv(["launch", "daemon", "start", "paper-printer", "extra"], output)

    assert exit_code != 0
    assert "Got unexpected extra argument" in output.getvalue()


def test_app_launch_workspace_prompt_has_default_launch_state(tmp_path) -> None:
    session = AppSession(
        stdout=StringIO(),
        context=_surface_context(tmp_path),
    )

    assert session.prompt() == "kairos/app> "
    assert session.handle("2") is False

    assert session.prompt() == "kairos/app/launch> "


def test_app_launch_daemon_is_navigation_context(tmp_path) -> None:
    stdout = StringIO()
    session = AppSession(
        stdout=stdout,
        context=_surface_context(tmp_path),
    )

    assert session.handle("launch daemon") is False

    assert session.prompt() == "kairos/app/launch/daemon> "
    output = stdout.getvalue()
    assert "Subcommands" in output
    assert "start" in output
    assert "status" in output
    assert "stop" in output


def test_app_initial_screen_renders_home_from_navigation(tmp_path) -> None:
    session = AppSession(
        stdout=StringIO(),
        context=_surface_context(tmp_path),
    )

    output = session.screen()

    assert "view home" in output
    assert "Products" in output
    assert "reference" in output
    assert "Commands" in output


def test_app_account_view_uses_navigation_subcommands(tmp_path) -> None:
    stdout = StringIO()
    session = AppSession(
        stdout=stdout,
        context=_surface_context(tmp_path),
    )

    assert session.handle("3") is False

    output = stdout.getvalue()
    assert "view account" in output
    assert "Subcommands" in output
    assert "list" in output
    assert "show" in output
    assert "Recent Launches" not in output


def test_app_order_context_does_not_inject_account(tmp_path) -> None:
    stdout = StringIO()
    calls: list[list[str]] = []

    def execute(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return 0, "ok\n"

    session = AppSession(
        stdout=stdout,
        context=_surface_context(tmp_path),
        command_executor=execute,
    )

    assert session.handle("order") is False
    assert session.prompt() == "kairos/app/order> "
    assert session.handle("open --account account1 --symbol BTC/USDT") is False

    assert calls[-1] == ["order", "open", "--account", "account1", "--symbol", "BTC/USDT"]


def test_app_reference_view_shows_reference_commands(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".kairos").mkdir()
    (tmp_path / ".kairos" / "kairos.toml").write_text(
        "\n".join(["schema_version = 1", "[project]", 'name = "demo"']),
        encoding="utf-8",
    )
    stdout = StringIO()
    session = AppSession(
        stdout=stdout,
        context=_surface_context(tmp_path),
    )

    assert session.handle("7") is False

    output = stdout.getvalue()
    assert "view reference" in output
    assert "Subcommands" in output
    assert "sync" in output
    assert "participants" in output
    assert "assets" in output
    assert "catalog" in output
    assert "Reference Panel" not in output


def test_app_context_screen_is_framed(tmp_path) -> None:
    stdout = StringIO()
    session = AppSession(
        stdout=stdout,
        context=_surface_context(tmp_path),
    )

    assert session.handle("reference") is False

    assert stdout.getvalue().startswith("-" * 72 + "\n")


def test_app_reference_numeric_subcommand_uses_current_context(tmp_path) -> None:
    session = AppSession(
        stdout=StringIO(),
        context=_surface_context(tmp_path),
    )

    assert session.handle("reference") is False
    assert session.handle("2") is False

    assert session.prompt() == "kairos/app/reference/participants> "


def test_app_reference_removed_legacy_refresh_context(tmp_path) -> None:
    stdout = StringIO()
    calls: list[list[str]] = []

    def execute(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return 2, "no such command\n"

    session = AppSession(
        stdout=stdout,
        context=_surface_context(tmp_path),
        command_executor=execute,
    )

    assert session.handle("reference refresh") is False

    assert session.prompt() == "kairos/app/reference> "
    assert calls == [["reference", "refresh"]]


def test_app_top_level_product_tail_executes_in_context(tmp_path) -> None:
    stdout = StringIO()
    calls: list[list[str]] = []

    def execute(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return 0, "ok\n"

    session = AppSession(
        stdout=stdout,
        context=_surface_context(tmp_path),
        command_executor=execute,
    )

    assert session.handle("reference assets list --type crypto") is False

    assert session.prompt() == "kairos/app/reference/assets> "
    assert calls == [["reference", "assets", "list", "--type", "crypto"]]


def test_app_reference_child_context_prefixes_commands(tmp_path) -> None:
    stdout = StringIO()
    calls: list[list[str]] = []

    def execute(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return 0, "ok\n"

    session = AppSession(
        stdout=stdout,
        context=_surface_context(tmp_path),
        command_executor=execute,
    )

    assert session.handle("reference") is False
    assert session.handle("assets") is False
    assert session.prompt() == "kairos/app/reference/assets> "
    assert session.handle("list --type crypto") is False

    assert calls == [["reference", "assets", "list", "--type", "crypto"]]


def test_app_command_output_is_framed(tmp_path) -> None:
    stdout = StringIO()

    def execute(argv: list[str]) -> tuple[int, str]:
        return 0, "ok\n"

    session = AppSession(
        stdout=stdout,
        context=_surface_context(tmp_path),
        command_executor=execute,
    )

    assert session.handle("reference assets list --type crypto") is False

    assert stdout.getvalue().endswith("--- kairospy reference assets list --type crypto\nok\n---\n")


def test_app_back_pops_one_context_level(tmp_path) -> None:
    session = AppSession(
        stdout=StringIO(),
        context=_surface_context(tmp_path),
    )

    assert session.handle("reference assets") is False
    assert session.prompt() == "kairos/app/reference/assets> "
    assert session.handle("back") is False
    assert session.prompt() == "kairos/app/reference> "
