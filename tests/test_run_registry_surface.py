from __future__ import annotations

from io import StringIO
import json
from pathlib import Path

from typer.testing import CliRunner

from kairospy.surface.cli.app import execute_argv
from kairospy.surface.interactive.shell import AppSession
from kairospy.surface.cli.commands.run import run_app
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
            "runs": (),
        }


def _surface_context(root) -> SurfaceContext:
    return SurfaceContext(project_facade=_ProjectFacade(root))


def test_run_list_reads_registered_runs(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    config_path = _write_run_config(tmp_path)

    register = CliRunner().invoke(run_app, ["register", "btc-sma", str(config_path)], catch_exceptions=False)
    result = CliRunner().invoke(run_app, ["list", "--format", "json"], catch_exceptions=False)

    assert register.exit_code == 0
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["count"] == 1
    assert payload["runs"][0]["name"] == "btc-sma"
    assert payload["runs"][0]["run_id"] == "bt-1"
    assert payload["runs"][0]["mode"] == "backtest"
    assert payload["runs"][0]["strategy"] == "strategy_mod:ConfiguredStrategy"

    text = CliRunner().invoke(run_app, ["list", "--format", "text"], catch_exceptions=False)

    assert "name     mode      run_id" in text.output
    assert "btc-sma  backtest  bt-1" in text.output


def test_run_instance_list_reads_rewritten_runtime_artifact_registry(tmp_path, monkeypatch) -> None:
    directory = tmp_path / "backtest" / "bt-1"
    directory.mkdir(parents=True)
    (directory / "summary.json").write_text(
        json.dumps({"run_id": "bt-1", "mode": "backtest", "event_count": 2}) + "\n",
        encoding="utf-8",
    )
    (directory / "run.log").write_text("strategy output\n", encoding="utf-8")

    result = CliRunner().invoke(run_app, ["instance", "list", "--root", str(tmp_path), "--format", "json"], catch_exceptions=False)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["count"] == 1
    assert payload["runs"][0]["run_id"] == "bt-1"
    assert payload["runs"][0]["mode"] == "backtest"
    assert payload["runs"][0]["log_file"] == str(directory / "run.log")

    filtered = CliRunner().invoke(
        run_app,
        ["instance", "list", "--root", str(tmp_path), "--run-id", "bt-1", "--format", "json"],
        catch_exceptions=False,
    )

    assert json.loads(filtered.output)["count"] == 1

    positional = CliRunner().invoke(
        run_app,
        ["instance", "list", "bt-1", "--root", str(tmp_path), "--format", "json"],
        catch_exceptions=False,
    )

    assert json.loads(positional.output)["count"] == 1

    text = CliRunner().invoke(run_app, ["instance", "list", "--root", str(tmp_path), "--format", "text"], catch_exceptions=False)

    assert "mode      run_id  status" in text.output
    assert "backtest  bt-1" in text.output
    assert str(directory / "run.log") not in text.output

    default_text = CliRunner().invoke(run_app, ["instance", "list", "--root", str(tmp_path)], catch_exceptions=False)

    assert default_text.output.startswith("Runs\n")
    assert "backtest  bt-1" in default_text.output

    monkeypatch.chdir(tmp_path)
    details = CliRunner().invoke(
        run_app,
        ["instance", "list", "--root", str(tmp_path), "--details", "--format", "text"],
        catch_exceptions=False,
    )

    assert "directory" in details.output
    assert "backtest/bt-1/run.log" in details.output
    assert str(directory / "run.log") not in details.output


def test_run_daemon_status_uses_artifact_registry(tmp_path) -> None:
    directory = tmp_path / "backtest" / "bt-1"
    directory.mkdir(parents=True)
    (directory / "summary.json").write_text(
        json.dumps({"run_id": "bt-1", "mode": "backtest", "event_count": 2}) + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        run_app,
        ["daemon", "status", "--root", str(tmp_path), "--run-id", "bt-1", "--format", "json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["runs"][0]["run_id"] == "bt-1"


def _write_workspace_manifest(root) -> None:
    (root / ".kairos").mkdir(parents=True, exist_ok=True)
    (root / ".kairos" / "kairos.toml").write_text("[project]\nname = \"test\"\n", encoding="utf-8")


def _write_run_config(root) -> Path:
    config_path = root / "run.toml"
    config_path.write_text(
        "\n".join(
            [
                "[run]",
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

    exit_code = execute_argv(["run", "daemon", "start", "paper-printer", "extra"], output)

    assert exit_code != 0
    assert "Got unexpected extra argument" in output.getvalue()


def test_app_run_workspace_prompt_has_default_run_state(tmp_path) -> None:
    session = AppSession(
        stdout=StringIO(),
        context=_surface_context(tmp_path),
    )

    assert session.prompt() == "kairos/app> "
    assert session.handle("2") is False

    assert session.prompt() == "kairos/app/run> "


def test_app_run_daemon_is_navigation_context(tmp_path) -> None:
    stdout = StringIO()
    session = AppSession(
        stdout=stdout,
        context=_surface_context(tmp_path),
    )

    assert session.handle("run daemon") is False

    assert session.prompt() == "kairos/app/run/daemon> "
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
    assert "Recent Runs" not in output


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

    assert session.handle("6") is False

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
