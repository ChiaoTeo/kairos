from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from kairospy.surface.cli.interactive.context import (
    create_context,
    go_back,
    go_home,
    print_context,
)
from kairospy.surface.cli.interactive.models import InteractiveContext
from kairospy.surface.console.models import ObserveSnapshot


def test_context_turns_health_issues_into_product_actions(capsys) -> None:
    owner = SimpleNamespace(
        workspace_id="trader",
        paths=SimpleNamespace(project_root=Path("/workspace/trader")),
    )
    snapshot = ObserveSnapshot(
        workspace_id="trader",
        components={
            "reference": {"status": "not_running"},
            "market": {"status": "not_running"},
        },
        launches=(
            {"launch_id": "alpha", "mode": "paper", "state": "failed"},
        ),
    )
    context = InteractiveContext(owner=owner, snapshot=snapshot, workspace_arg=None)

    print_context(context, ({"status": "invalid"},))

    text = capsys.readouterr().out
    assert "运行准备  0 个 Launch 可启动 · 0 个需要处理" in text
    assert "运行资源  0 个已验证 · 1 个待处理" in text
    assert "正在运行  0 个策略 · 0 个必需服务不可用" in text
    assert "最近结果  1 个策略失败 · 0 个策略完成" in text
    assert "输入 diagnose 排查最近失败" in text
    assert "输入 resources 检查运行资源" in text


def test_context_reports_stopped_services_only_when_an_active_launch_requires_them(
    capsys,
) -> None:
    owner = SimpleNamespace(
        workspace_id="trader",
        paths=SimpleNamespace(project_root=Path("/workspace/trader")),
    )
    snapshot = ObserveSnapshot(
        workspace_id="trader",
        components={
            "reference": {"status": "not_running"},
            "market": {"status": "not_running"},
        },
        launches=({"launch_id": "alpha", "mode": "paper", "state": "running"},),
    )

    print_context(InteractiveContext(owner, snapshot, None), ())

    text = capsys.readouterr().out
    assert "正在运行  1 个策略 · 2 个必需服务不可用" in text
    assert "输入 fix 修复运行依赖" in text


def test_context_uses_discovered_workspace_for_child_commands(monkeypatch) -> None:
    discovered_root = Path("/tmp/discovered/.kairos")
    owner = SimpleNamespace(paths=SimpleNamespace(root=discovered_root))
    monkeypatch.setattr(
        "kairospy.surface.cli.interactive.context.resolve_workspace",
        lambda _workspace: owner,
    )
    monkeypatch.setattr(
        "kairospy.surface.cli.interactive.context.read_snapshot",
        lambda _owner: None,
    )

    context = create_context(None)

    assert context.owner is owner
    assert context.workspace_arg == discovered_root


def test_context_preserves_explicit_workspace(monkeypatch) -> None:
    explicit_root = Path("/tmp/explicit")
    owner = SimpleNamespace(paths=SimpleNamespace(root=Path("/tmp/explicit/.kairos")))
    monkeypatch.setattr(
        "kairospy.surface.cli.interactive.context.resolve_workspace",
        lambda _workspace: owner,
    )
    monkeypatch.setattr(
        "kairospy.surface.cli.interactive.context.read_snapshot",
        lambda _owner: None,
    )

    context = create_context(explicit_root)

    assert context.workspace_arg == explicit_root


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
    interactive_context.selected_market_provider = {"provider": "binance"}
    interactive_context.selected_reference = object()
    interactive_context.selected_reference_kind = "asset"
    go_home(interactive_context)
    assert interactive_context.shell_path == ()
    assert interactive_context.selected_launch is None
    assert interactive_context.selected_account is None
    assert interactive_context.selected_service is None
    assert interactive_context.selected_launch_instance is None
    assert interactive_context.selected_market is None
    assert interactive_context.selected_market_provider is None
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
    interactive_context.selected_market_provider = {"provider": "binance"}

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
    assert interactive_context.selected_market_provider is None


def test_back_from_direct_market_target_returns_to_market_center(
    interactive_context,
) -> None:
    interactive_context.shell_path = ("market", "AAPL")
    interactive_context.selected_market = object()
    interactive_context.selected_market_provider = {"provider": "massive"}

    go_back(interactive_context)

    assert interactive_context.shell_path == ("market",)
    assert interactive_context.selected_market is None
    assert interactive_context.selected_market_provider is None
