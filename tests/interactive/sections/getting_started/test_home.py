from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand, ShellControl
from kairospy.surface.cli.interactive.sections.getting_started import home


def test_home_groups_complete_product_surface(interactive_context, capsys) -> None:
    home.print_menu(interactive_context)
    home.print_help(interactive_context)
    text = capsys.readouterr().out
    for label in (
        "策略运行",
        "交易管理",
        "市场目录",
        "数据与研究",
        "系统与集成",
        "诊断与观测",
        "项目与帮助",
    ):
        assert label in text
    assert "行情（请从" not in text
    assert "  8." not in text


def test_home_numeric_and_text_navigation(interactive_context) -> None:
    assert home.handle(interactive_context, ("risk",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("risk",)
    interactive_context.shell_path = ()
    assert home.handle(interactive_context, ("2",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("trade-control",)
    assert home.handle(interactive_context, ("3",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("risk",)


def test_home_quickstart_is_workspace_independent(interactive_context) -> None:
    assert home.handle(interactive_context, ("7",)) is ShellControl.HANDLED
    command = home.handle(interactive_context, ("2",))
    assert isinstance(command, GuidedCommand)
    assert command.needs_workspace is False


def test_home_group_menus_expose_technical_sections_at_second_level(
    interactive_context, capsys
) -> None:
    for path, labels in (
        (("data-research",), ("数据", "研究")),
        (("trade-control",), ("账户", "Execution", "Risk", "Capital")),
        (("operations",), ("系统服务", "Provider", "通知", "高级配置")),
        (("project-help",), ("项目", "命令地图")),
    ):
        interactive_context.shell_path = path
        home.print_menu(interactive_context)
        text = capsys.readouterr().out
        assert all(label in text for label in labels)
