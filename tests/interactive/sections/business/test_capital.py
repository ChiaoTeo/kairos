from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand
from kairospy.surface.cli.interactive.sections.business import capital


def test_capital_menu_help_and_current_view(interactive_context, capsys) -> None:
    capital.print_menu(interactive_context)
    capital.print_help(interactive_context)
    numeric = capital.handle(interactive_context, ("6",))
    text = capital.handle(interactive_context, ("current",))
    assert isinstance(numeric, GuidedCommand)
    assert isinstance(text, GuidedCommand)
    assert numeric.argv == text.argv
    assert numeric.argv[:4] == ("system", "component", "capital", "current")
    assert "Capital" in capsys.readouterr().out
