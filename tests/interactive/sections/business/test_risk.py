from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand
from kairospy.surface.cli.interactive.sections.business import risk


def test_risk_menu_help_and_connected_reservations(
    interactive_context, capsys
) -> None:
    risk.print_menu(interactive_context)
    risk.print_help(interactive_context)
    numeric = risk.handle(interactive_context, ("6",))
    text = risk.handle(interactive_context, ("reservations",))
    assert isinstance(numeric, GuidedCommand)
    assert isinstance(text, GuidedCommand)
    assert numeric.argv == text.argv
    assert numeric.argv[:4] == ("system", "component", "risk", "reservations")
    assert "Risk" in capsys.readouterr().out
