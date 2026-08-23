from __future__ import annotations

from kairospy.surface.cli.interactive.context import go_back, go_home


def test_back_clears_selection_owned_by_path(interactive_context) -> None:
    interactive_context.shell_path = ("trade", "accounts", "paper")
    interactive_context.selected_account = "paper"
    go_back(interactive_context)
    assert interactive_context.shell_path == ("trade", "accounts")
    assert interactive_context.selected_account is None


def test_back_from_account_list_preserves_trade_parent(interactive_context) -> None:
    interactive_context.shell_path = ("trade", "accounts")
    go_back(interactive_context)
    assert interactive_context.shell_path == ("trade",)


def test_back_clears_order_owned_segment_and_symbol_selections(
    interactive_context,
) -> None:
    interactive_context.shell_path = (
        "trade",
        "accounts",
        "main",
        "orders",
        "history",
    )
    interactive_context.selected_account = "main"
    interactive_context.selected_account_segment = "spot"
    interactive_context.selected_order_symbol = "BTCUSDT"

    go_back(interactive_context)
    assert interactive_context.shell_path == ("trade", "accounts", "main", "orders")
    assert interactive_context.selected_account_segment == "spot"
    assert interactive_context.selected_order_symbol is None

    go_back(interactive_context)
    assert interactive_context.shell_path == ("trade", "accounts", "main")
    assert interactive_context.selected_account_segment is None


def test_home_clears_all_product_selections(interactive_context) -> None:
    interactive_context.shell_path = ("reference", "assets", "BTC")
    interactive_context.selected_launch = "demo"
    interactive_context.selected_account = "paper"
    interactive_context.selected_service = "market"
    interactive_context.selected_launch_instance = "instance-1"
    interactive_context.selected_market = object()
    interactive_context.selected_market_source = {"source_id": "source-1"}
    interactive_context.selected_reference = object()
    interactive_context.selected_reference_kind = "asset"
    go_home(interactive_context)
    assert interactive_context.shell_path == ()
    assert interactive_context.selected_launch is None
    assert interactive_context.selected_account is None
    assert interactive_context.selected_service is None
    assert interactive_context.selected_launch_instance is None
    assert interactive_context.selected_market is None
    assert interactive_context.selected_market_source is None
    assert interactive_context.selected_reference is None
    assert interactive_context.selected_reference_kind is None


def test_back_from_launch_market_clears_only_market_scope_selections(
    interactive_context,
) -> None:
    interactive_context.shell_path = (
        "launch",
        "demo",
        "instances",
        "instance-1",
        "components",
        "market",
    )
    interactive_context.selected_launch = "demo"
    interactive_context.selected_launch_instance = "instance-1"
    interactive_context.selected_market = object()
    interactive_context.selected_market_source = {"source_id": "source-1"}

    go_back(interactive_context)

    assert interactive_context.shell_path == (
        "launch",
        "demo",
        "instances",
        "instance-1",
        "components",
    )
    assert interactive_context.selected_launch == "demo"
    assert interactive_context.selected_launch_instance == "instance-1"
    assert interactive_context.selected_market is None
    assert interactive_context.selected_market_source is None
