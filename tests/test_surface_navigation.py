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


def test_navigation_resolves_root_numeric_tokens_from_filtered_products() -> None:
    session = AppSession()

    assert resolve_token("6", names=session._root_names()) == "reference"
    assert resolve_token("1", names=session._root_names()) == "project"
