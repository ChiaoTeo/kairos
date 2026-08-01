from __future__ import annotations

from io import StringIO

from kairospy.surface.cli import app
from typer.testing import CliRunner
from kairospy.surface.interactive.navigation import match_group_context, resolve_token, root_command
from kairospy.surface.interactive.shell import AppSession


def test_interactive_product_order_is_stable() -> None:
    session = AppSession()

    assert [view.name for view in session._root_views()] == [
        "project",
        "launch",
        "account",
        "credential",
        "order",
        "market",
        "reference",
        "strategy",
        "config",
        "timeline",
    ]


def test_interactive_help_exposes_only_stable_shell_entrypoint() -> None:
    result = CliRunner().invoke(app, ["--help"], catch_exceptions=False)

    assert "shell" in result.output
    assert "app" not in result.output
    assert "tui" not in result.output


def test_shell_surface_labels_global_commands() -> None:
    screen = AppSession(surface_name="shell").screen()

    assert "reload shell state" in screen
    assert "exit shell" in screen


def test_navigation_matches_longest_group_context() -> None:
    root = root_command(app)
    names = AppSession()._root_names()

    assert match_group_context(root, (), ["reference", "assets", "list"], root_names=names) == (("reference", "assets"), ["list"])
    assert match_group_context(root, ("reference",), ["assets"], root_names=names) == (("reference", "assets"), [])
    assert match_group_context(root, ("reference",), ["2"], root_names=names) == (("reference", "participants"), [])


def test_navigation_does_not_enter_leaf_command_context() -> None:
    root = root_command(app)
    names = AppSession()._root_names()

    assert match_group_context(root, ("reference", "assets"), ["list", "--type", "crypto"], root_names=names) == (
        None,
        ["list", "--type", "crypto"],
    )


def test_navigation_unknown_token_falls_back_to_current_context() -> None:
    root = root_command(app)
    names = AppSession()._root_names()

    assert match_group_context(root, ("reference",), ["does-not-exist"], root_names=names) == (None, ["does-not-exist"])


def test_navigation_context_leaf_numeric_does_not_fall_back_to_root_product() -> None:
    root = root_command(app)
    names = AppSession()._root_names()

    assert match_group_context(root, ("config",), ["2"], root_names=names) == (None, ["2"])


def test_navigation_context_unknown_numeric_does_not_fall_back_to_root_product() -> None:
    root = root_command(app)
    names = AppSession()._root_names()

    assert match_group_context(root, ("config",), ["99"], root_names=names) == (None, ["99"])


def test_navigation_resolves_root_numeric_tokens_from_filtered_products() -> None:
    session = AppSession()

    assert resolve_token("7", names=session._root_names()) == "reference"
    assert resolve_token("1", names=session._root_names()) == "project"


def test_context_numeric_leaf_command_executes_resolved_command() -> None:
    calls: list[list[str]] = []
    session = AppSession(command_executor=lambda argv: calls.append(argv) or (0, ""))
    session.handle("launch targets")

    assert session.handle("4") is False

    assert calls == [["launch", "targets", "list"]]


def test_launch_context_exposes_product_domains_not_internal_cli() -> None:
    session = AppSession()
    session.handle("launch")

    names = [view.name for view in session._child_views(("launch",))]

    assert names == ["system", "daemon", "targets", "run", "observe", "diagnose", "replay"]
    assert "system" in names
    assert "cli" not in names


def test_context_numeric_leaf_command_preserves_arguments() -> None:
    calls: list[list[str]] = []
    session = AppSession(command_executor=lambda argv: calls.append(argv) or (0, ""))
    session.handle("launch targets")

    assert session.handle("4 --format json") is False

    assert calls == [["launch", "targets", "list", "--format", "json"]]


def test_config_context_numeric_leaf_command_executes_config_show() -> None:
    calls: list[list[str]] = []
    session = AppSession(command_executor=lambda argv: calls.append(argv) or (0, ""))
    session.handle("config")

    assert session.handle("2") is False

    assert session.context_path == ("config",)
    assert calls == [["config", "show"]]


def test_shell_account_create_enters_interactive_wizard(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    CliRunner().invoke(app, ["project", "init", str(project)], catch_exceptions=False)
    monkeypatch.chdir(project)
    stdout = StringIO()
    calls: list[list[str]] = []
    session = AppSession(stdout=stdout, command_executor=lambda argv: calls.append(argv) or (0, ""))

    assert session.handle("account create") is False
    assert session.prompt() == "kairos/app/account/create> "
    for line in ["okx_testnet", "okx", "testnet", "", "", "", "", "skip", "y"]:
        assert session.handle(line) is False

    path = project / ".kairos" / "accounts" / "okx_testnet.toml"
    text = path.read_text(encoding="utf-8")
    assert calls == []
    assert session.account_create_wizard is None
    assert 'broker = "okx"' in text
    assert 'environment = "testnet"' in text
    assert 'market = "spot"' not in text
    assert "created account:" in stdout.getvalue()


def test_shell_account_create_direct_uses_command_executor() -> None:
    calls: list[list[str]] = []
    session = AppSession(command_executor=lambda argv: calls.append(argv) or (0, "created"))
    session.handle("account")

    assert session.handle("create --direct binance_paper --provider binance --environment paper") is False

    assert session.account_create_wizard is None
    assert calls == [["account", "create", "binance_paper", "--provider", "binance", "--environment", "paper"]]


def test_shell_streaming_executor_writes_during_command() -> None:
    stdout = StringIO()
    calls: list[list[str]] = []

    def execute(argv: list[str], output) -> int:
        calls.append(argv)
        output.write("streamed\n")
        return 0

    session = AppSession(stdout=stdout, streaming_command_executor=execute)
    session.handle("account")

    assert session.handle("balance main --book spot") is False

    assert calls == [["account", "balance", "main", "--book", "spot"]]
    assert "--- kairospy account balance main --book spot\nstreamed\n---\n" in stdout.getvalue()


def test_shell_streaming_executor_errors_do_not_exit_session() -> None:
    stdout = StringIO()

    def execute(argv: list[str], output) -> int:
        raise RuntimeError("network unavailable")

    session = AppSession(stdout=stdout, streaming_command_executor=execute)
    session.handle("reference")

    assert session.handle("sync binance") is False
    assert session.context_path == ("reference", "sync")
    assert "error: network unavailable" in stdout.getvalue()
    assert "Command exited with status 1" in stdout.getvalue()
