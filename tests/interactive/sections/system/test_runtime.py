from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand, ShellControl
from kairospy.surface.cli.interactive.sections.system import runtime


def test_runtime_menu_help_and_component_navigation(
    interactive_context, capsys
) -> None:
    interactive_context.shell_path = ("system",)
    runtime.print_menu(interactive_context)
    runtime.print_help(interactive_context)
    assert runtime.handle(interactive_context, ("2",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("system", "market")
    text = capsys.readouterr().out
    assert "系统服务" in text
    assert "risk" not in text
    assert "capital" not in text


def test_runtime_rejects_launch_owned_components(interactive_context) -> None:
    interactive_context.shell_path = ("system",)
    assert runtime.handle(interactive_context, ("risk",)) is None
    assert runtime.handle(interactive_context, ("capital",)) is None
    assert interactive_context.shell_path == ("system",)


def test_runtime_restart_aliases_are_dangerous(interactive_context) -> None:
    interactive_context.shell_path = ("system", "market")
    numeric = runtime.handle(interactive_context, ("9",))
    text = runtime.handle(interactive_context, ("restart",))
    assert isinstance(numeric, GuidedCommand)
    assert isinstance(text, GuidedCommand)
    assert numeric == text
    assert numeric.dangerous is True
