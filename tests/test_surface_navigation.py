from __future__ import annotations

from io import StringIO

from kairospy.surface.cli import app
from typer.testing import CliRunner
from kairospy.surface.interactive.navigation import child_names, match_group_context, resolve_token, root_command
from kairospy.surface.interactive.shell import AppSession


def test_interactive_product_order_is_stable() -> None:
    session = AppSession()

    assert [view.name for view in session._root_views()] == [
        "project",
        "launch",
        "account",
        "order",
        "market",
        "catalog",
        "system",
    ]


def test_interactive_child_orders_are_stable_for_product_contexts() -> None:
    root = root_command(app)

    expected = {
        ("project",): ("init", "status", "doctor", "config"),
        ("project", "config"): ("paths", "show", "manifest", "doctor", "explain", "operations", "profile"),
        ("launch",): ("targets", "diagnose", "replay", "timeline", "start", "stop", "status", "logs", "artifacts", "instances", "attach"),
        ("launch", "targets"): ("add", "remove", "index", "list", "browse"),
        ("launch", "diagnose"): ("validate", "explain"),
        ("launch", "replay"): ("events",),
        ("launch", "timeline"): ("list", "export", "open", "api"),
        ("account",): (
            "list",
            "browse",
            "schemas",
            "schema",
            "create",
            "modify",
            "delete",
            "remove",
            "show",
            "doctor",
            "credential",
            "query",
            "trade-lock",
        ),
        ("account", "credential"): ("add", "list", "create", "show", "delete", "remove"),
        ("account", "query"): ("balance", "current", "balances", "positions", "open-orders", "snapshot"),
        ("account", "trade-lock"): ("status", "list", "show", "release"),
        ("order",): ("open", "list", "browse", "closed", "history", "place", "cancel", "replace", "show", "inspect"),
        ("market",): ("source", "data", "dataset", "stream"),
        ("market", "source"): ("capabilities", "check", "doctor"),
        ("market", "data"): ("download", "prefetch"),
        ("market", "dataset"): ("list", "inspect", "alias", "prune", "read"),
        ("market", "stream"): ("replay", "watch", "persist"),
        ("catalog",): ("sync", "participants", "assets", "markets", "events", "view", "query", "search", "show", "status"),
        ("catalog", "sync"): ("binance", "hyperliquid", "massive"),
        ("catalog", "participants"): ("brokers", "exchanges", "providers"),
        ("catalog", "assets"): ("add", "list", "browse", "show"),
        ("catalog", "markets"): ("list", "browse", "resolve"),
        ("catalog", "events"): ("sync",),
        ("system",): ("status", "inspect", "attach", "up", "down", "restart", "account", "command"),
        ("system", "account"): ("trade-status", "trade-acquire", "trade-release"),
    }

    for path, names in expected.items():
        assert child_names(root, path) == names


def test_interactive_help_exposes_only_stable_shell_entrypoint() -> None:
    result = CliRunner().invoke(app, ["--help"], catch_exceptions=False)

    assert "shell" in result.output
    assert "catalog" in result.output
    assert "app" not in result.output
    assert "tui" not in result.output
    assert "│ credential" not in result.output
    assert "│ reference" not in result.output
    assert "│ strategy" not in result.output
    assert "│ config" not in result.output
    assert "│ timeline" not in result.output


def test_app_compatibility_entrypoint_is_not_registered() -> None:
    result = CliRunner().invoke(app, ["app", "--help"], catch_exceptions=False)

    assert result.exit_code != 0
    assert "No such command" in result.output


def test_shell_surface_labels_global_commands() -> None:
    screen = AppSession(surface_name="shell").screen()

    assert "show shell command history" in screen
    assert "reload shell state" in screen
    assert "exit shell" in screen


def test_shell_history_command_shows_persisted_root_history(tmp_path, monkeypatch) -> None:
    from kairospy.surface.interactive.line_reader import _FilteredFileHistory

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    history = _FilteredFileHistory(tmp_path / "kairospy" / "shell_history", max_history=1000)
    history.append_string("account")
    history.append_string("market")
    stdout = StringIO()
    session = AppSession(stdout=stdout)

    assert session.handle("history 1") is False

    output = stdout.getvalue()
    assert "market" in output
    assert "account" not in output


def test_shell_history_command_works_in_context_without_history_subcommand(tmp_path, monkeypatch) -> None:
    from kairospy.surface.interactive.line_reader import _FilteredFileHistory

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    history = _FilteredFileHistory(tmp_path / "kairospy" / "shell_history", max_history=1000)
    history.append_string("account")
    stdout = StringIO()
    calls: list[list[str]] = []
    session = AppSession(stdout=stdout, command_executor=lambda argv: calls.append(argv) or (0, ""))
    session.handle("account")

    assert session.handle("history") is False

    assert "1  account" in stdout.getvalue()
    assert calls == []


def test_shell_history_command_preserves_product_history_subcommand() -> None:
    calls: list[list[str]] = []
    session = AppSession(command_executor=lambda argv: calls.append(argv) or (0, ""))
    session.handle("order")

    assert session.handle("history --account main") is False

    assert calls == [["order", "history", "--account", "main"]]


def test_shell_history_clear_removes_persisted_history(tmp_path, monkeypatch) -> None:
    from kairospy.surface.interactive.line_reader import _FilteredFileHistory, default_history_path

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    history = _FilteredFileHistory(default_history_path(), max_history=1000)
    history.append_string("account")
    session = AppSession()

    assert session.handle("history clear") is False

    assert not default_history_path().exists()


def test_navigation_matches_longest_group_context() -> None:
    root = root_command(app)
    names = AppSession()._root_names()

    assert match_group_context(root, (), ["catalog", "assets", "list"], root_names=names) == (("catalog", "assets"), ["list"])
    assert match_group_context(root, ("catalog",), ["assets"], root_names=names) == (("catalog", "assets"), [])
    assert match_group_context(root, ("catalog",), ["2"], root_names=names) == (("catalog", "participants"), [])


def test_navigation_does_not_enter_leaf_command_context() -> None:
    root = root_command(app)
    names = AppSession()._root_names()

    assert match_group_context(root, ("catalog", "assets"), ["list", "--type", "crypto"], root_names=names) == (
        None,
        ["list", "--type", "crypto"],
    )


def test_navigation_unknown_token_falls_back_to_current_context() -> None:
    root = root_command(app)
    names = AppSession()._root_names()

    assert match_group_context(root, ("catalog",), ["does-not-exist"], root_names=names) == (None, ["does-not-exist"])


def test_navigation_context_leaf_numeric_does_not_fall_back_to_root_product() -> None:
    root = root_command(app)
    names = AppSession()._root_names()

    assert match_group_context(root, ("project", "config"), ["2"], root_names=names) == (None, ["2"])


def test_navigation_context_unknown_numeric_does_not_fall_back_to_root_product() -> None:
    root = root_command(app)
    names = AppSession()._root_names()

    assert match_group_context(root, ("project", "config"), ["99"], root_names=names) == (None, ["99"])


def test_navigation_resolves_root_numeric_tokens_from_filtered_products() -> None:
    session = AppSession()

    assert resolve_token("6", names=session._root_names()) == "catalog"
    assert resolve_token("7", names=session._root_names()) == "system"
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

    assert names == ["targets", "diagnose", "replay", "timeline", "start", "stop", "status", "logs", "artifacts", "instances", "attach"]
    assert "system" not in names
    assert "daemon" not in names
    assert "cli" not in names


def test_launch_system_token_opens_top_level_system_context() -> None:
    stdout = StringIO()
    calls: list[list[str]] = []
    session = AppSession(stdout=stdout, command_executor=lambda argv: calls.append(argv) or (0, ""))
    session.handle("launch")

    assert session.handle("system") is False

    assert session.context_path == ("system",)
    assert calls == []
    assert "System" in stdout.getvalue()
    assert "Entered system mode" not in stdout.getvalue()


def test_top_level_system_token_opens_system_cli_context() -> None:
    stdout = StringIO()
    session = AppSession(stdout=stdout, command_executor=lambda argv: (0, ""))

    assert session.handle("system") is False

    assert session.context_path == ("system",)
    assert session.prompt() == "kairos/app/system> "
    assert "System" in stdout.getvalue()
    assert "Entered system mode" not in stdout.getvalue()


def test_action_commands_do_not_become_shell_context() -> None:
    calls: list[list[str]] = []
    session = AppSession(command_executor=lambda argv: calls.append(argv) or (0, ""))

    assert session.handle("catalog assets browse --page-size 1") is False

    assert session.context_path == ("catalog", "assets")
    assert calls == [["catalog", "assets", "browse", "--page-size", "1"]]


def test_shell_context_marks_browse_as_fullscreen_entry() -> None:
    session = AppSession()
    session.handle("catalog assets")

    screen = session.screen()

    assert "browse" in screen
    assert "open full-screen resource browser" in screen


def test_shell_tty_browse_command_omits_command_output_wrapper() -> None:
    class TtyOutput(StringIO):
        def isatty(self) -> bool:
            return True

    stdout = TtyOutput()
    calls: list[list[str]] = []
    session = AppSession(
        stdout=stdout,
        streaming_command_executor=lambda argv, output: calls.append(argv) or 0,
    )
    session.handle("catalog assets")

    assert session.handle("browse") is False

    assert calls == [["catalog", "assets", "browse"]]
    assert "--- kairospy catalog assets browse" not in stdout.getvalue()


def test_shell_browse_entry_passes_options_to_cli_executor() -> None:
    calls: list[list[str]] = []
    session = AppSession(command_executor=lambda argv: calls.append(argv) or (0, ""))
    session.handle("catalog assets")

    assert session.handle("browse --page-size 7 --query \"[?symbol == 'BTC']\"") is False

    assert calls == [["catalog", "assets", "browse", "--page-size", "7", "--query", "[?symbol == 'BTC']"]]


def test_system_attach_action_does_not_become_shell_context() -> None:
    calls: list[list[str]] = []
    session = AppSession(command_executor=lambda argv: calls.append(argv) or (0, ""))

    assert session.handle("system attach") is False

    assert session.context_path == ("system",)
    assert calls == [["system", "attach"]]


def test_context_numeric_leaf_command_preserves_arguments() -> None:
    calls: list[list[str]] = []
    session = AppSession(command_executor=lambda argv: calls.append(argv) or (0, ""))
    session.handle("launch targets")

    assert session.handle("4 --format json") is False

    assert calls == [["launch", "targets", "list", "--format", "json"]]


def test_project_config_context_numeric_leaf_command_executes_config_show() -> None:
    calls: list[list[str]] = []
    session = AppSession(command_executor=lambda argv: calls.append(argv) or (0, ""))
    session.handle("project config")

    assert session.handle("2") is False

    assert session.context_path == ("project", "config")
    assert calls == [["project", "config", "show"]]


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


def test_account_create_wizard_uses_broker_as_internal_account_identity() -> None:
    from kairospy.surface.interactive.account import AccountCreateWizard

    wizard = AccountCreateWizard()

    assert wizard.current == "account_id"
    wizard.handle("binance_paper")
    assert wizard.current == "broker"
    wizard.handle("binance")

    assert wizard.fields["broker"] == "binance"
    assert "provider" not in wizard.fields


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

    assert session.handle("query balance main --book spot") is False

    assert calls == [["account", "query", "balance", "main", "--book", "spot"]]
    assert "--- kairospy account query balance main --book spot\nstreamed\n---\n" in stdout.getvalue()


def test_shell_streaming_executor_errors_do_not_exit_session() -> None:
    stdout = StringIO()

    def execute(argv: list[str], output) -> int:
        raise RuntimeError("network unavailable")

    session = AppSession(stdout=stdout, streaming_command_executor=execute)
    session.handle("catalog")

    assert session.handle("sync binance") is False
    assert session.context_path == ("catalog", "sync")
    assert "error: network unavailable" in stdout.getvalue()
    assert "Command exited with status 1" in stdout.getvalue()
