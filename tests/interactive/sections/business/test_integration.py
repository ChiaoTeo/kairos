from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand
from kairospy.surface.cli.interactive.sections.business import integration


def test_integration_menu_help_and_capability_command(
    interactive_context, capsys
) -> None:
    integration.print_menu(interactive_context)
    integration.print_help(interactive_context)
    numeric = integration.handle(interactive_context, ("1",))
    text = integration.handle(interactive_context, ("capabilities",))
    assert isinstance(numeric, GuidedCommand)
    assert isinstance(text, GuidedCommand)
    assert numeric.argv == text.argv == ("integration", "--help")
    assert numeric.needs_workspace is False
    assert "Provider" in capsys.readouterr().out
