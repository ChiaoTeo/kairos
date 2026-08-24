from __future__ import annotations

from kairospy.surface.cli.interactive.models import CommandExecution, GuidedCommand
from kairospy.surface.cli.interactive.sections.strategy import observe


def test_observe_menu_help_and_aliases(interactive_context, capsys) -> None:
    observe.print_menu(interactive_context)
    observe.print_help(interactive_context)
    numeric = observe.handle(interactive_context, ("1",))
    text = observe.handle(interactive_context, ("open",))
    assert isinstance(numeric, GuidedCommand)
    assert isinstance(text, GuidedCommand)
    assert numeric == text
    assert numeric.execution is CommandExecution.STREAMING
    assert "诊断与观测" in capsys.readouterr().out
