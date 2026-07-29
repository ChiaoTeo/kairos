from __future__ import annotations

from kairospy.surface.cli import app
from kairospy.surface.interactive.navigation import match_group_context, resolve_token, root_command
from kairospy.surface.interactive.shell import AppSession


def test_interactive_product_order_is_stable() -> None:
    session = AppSession()

    assert [view.name for view in session._root_views()] == [
        "project",
        "run",
        "account",
        "order",
        "market",
        "reference",
        "strategy",
        "config",
    ]


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

    assert resolve_token("6", names=session._root_names()) == "reference"
    assert resolve_token("1", names=session._root_names()) == "project"


def test_context_numeric_leaf_command_executes_resolved_command() -> None:
    calls: list[list[str]] = []
    session = AppSession(command_executor=lambda argv: calls.append(argv) or (0, ""))
    session.handle("run")

    assert session.handle("13") is False

    assert calls == [["run", "list"]]


def test_context_numeric_leaf_command_preserves_arguments() -> None:
    calls: list[list[str]] = []
    session = AppSession(command_executor=lambda argv: calls.append(argv) or (0, ""))
    session.handle("run")

    assert session.handle("13 --format json") is False

    assert calls == [["run", "list", "--format", "json"]]


def test_config_context_numeric_leaf_command_executes_config_show() -> None:
    calls: list[list[str]] = []
    session = AppSession(command_executor=lambda argv: calls.append(argv) or (0, ""))
    session.handle("config")

    assert session.handle("2") is False

    assert session.context_path == ("config",)
    assert calls == [["config", "show"]]
