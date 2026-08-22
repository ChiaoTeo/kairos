from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand, ShellControl
from kairospy.surface.cli.interactive.sections.system import runtime


def test_runtime_menu_help_and_component_navigation(
    interactive_context, capsys
) -> None:
    interactive_context.shell_path = ("system",)
    runtime.print_menu(interactive_context)
    runtime.print_help(interactive_context)
    assert runtime.handle(interactive_context, ("3",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("system", "risk")
    assert "系统服务" in capsys.readouterr().out


def test_runtime_restart_aliases_are_dangerous(interactive_context) -> None:
    interactive_context.shell_path = ("system", "market")
    numeric = runtime.handle(interactive_context, ("9",))
    text = runtime.handle(interactive_context, ("restart",))
    assert isinstance(numeric, GuidedCommand)
    assert isinstance(text, GuidedCommand)
    assert numeric == text
    assert numeric.dangerous is True
