from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand
from kairospy.surface.cli.interactive.sections.getting_started import project


def test_project_menu_help_and_status_aliases(interactive_context, capsys) -> None:
    project.print_menu(interactive_context)
    project.print_help(interactive_context)
    numeric = project.handle(interactive_context, ("2",))
    text = project.handle(interactive_context, ("status",))
    assert isinstance(numeric, GuidedCommand)
    assert isinstance(text, GuidedCommand)
    assert numeric == text
    assert "项目" in capsys.readouterr().out


def test_project_scaffold_is_dangerous(interactive_context, monkeypatch) -> None:
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "backtest")
    command = project.handle(interactive_context, ("scaffold",))
    assert isinstance(command, GuidedCommand)
    assert command.dangerous is True
