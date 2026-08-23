from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand, ShellControl
from kairospy.surface.cli.interactive.sections.getting_started import home


def test_home_groups_complete_product_surface(interactive_context, capsys) -> None:
    home.print_menu(interactive_context)
    home.print_help(interactive_context)
    text = capsys.readouterr().out
    for label in (
        "市场行情",
        "策略运行",
        "交易管理",
        "市场目录",
        "数据与研究",
        "系统与配置",
    ):
        assert label in text
    assert "?. 帮助" in text
    assert "诊断与观测" not in text
    assert "项目与帮助" not in text


def test_home_numeric_and_text_navigation(interactive_context) -> None:
    assert home.handle(interactive_context, ("risk",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("risk",)
    interactive_context.shell_path = ()
    assert home.handle(interactive_context, ("4",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("trade",)
    assert home.handle(interactive_context, ("1",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("trade", "accounts")


def test_home_market_navigation_is_standalone(interactive_context) -> None:
    assert home.handle(interactive_context, ("market",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("market",)
    interactive_context.shell_path = ()
    assert home.handle(interactive_context, ("1",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("market",)


def test_home_quickstart_is_workspace_independent(interactive_context) -> None:
    command = home.handle(interactive_context, ("map",))
    assert isinstance(command, GuidedCommand)
    assert command.needs_workspace is False


def test_removed_numeric_entries_do_not_route(interactive_context) -> None:
    for key in ("7", "8"):
        assert home.handle(interactive_context, (key,)) is None
        assert interactive_context.shell_path == ()


def test_home_group_menus_expose_technical_sections_at_second_level(
    interactive_context, capsys
) -> None:
    for path, labels in (
        (("data-research",), ("数据", "研究")),
        (("operations",), ("项目工作区", "系统服务", "通知", "高级配置", "系统诊断")),
        (("trade",), ("账户与订单", "风险管理", "资金管理")),
        (("strategy",), ("运行列表与控制", "运行观测台", "运行快照")),
    ):
        interactive_context.shell_path = path
        home.print_menu(interactive_context)
        text = capsys.readouterr().out
        assert all(label in text for label in labels)


def test_operations_menu_does_not_expose_provider_integration(
    interactive_context, capsys
) -> None:
    interactive_context.shell_path = ("operations",)
    home.print_menu(interactive_context)
    assert "Provider" not in capsys.readouterr().out
    assert home.handle(interactive_context, ("integration",)) is None


def test_trade_parent_exposes_only_account_selection(
    interactive_context, capsys
) -> None:
    interactive_context.shell_path = ("trade",)
    home.print_menu(interactive_context)
    home.print_help(interactive_context)
    text = capsys.readouterr().out
    assert "账户与订单" in text
    assert "策略运行" not in text
    assert "Execution" not in text
    assert "Risk" not in text
    assert "Capital" not in text


def test_removed_trade_control_route_has_no_compatibility_entry(
    interactive_context,
) -> None:
    assert home.handle(interactive_context, ("trade-control",)) is None
    assert interactive_context.shell_path == ()
