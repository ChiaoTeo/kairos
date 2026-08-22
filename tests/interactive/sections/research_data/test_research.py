from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand
from kairospy.surface.cli.interactive.sections.research_data import research


def test_research_menu_help_and_lock_aliases(
    interactive_context, monkeypatch, capsys
) -> None:
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "research-plan.json")
    research.print_menu(interactive_context)
    research.print_help(interactive_context)
    numeric = research.handle(interactive_context, ("1",))
    text = research.handle(interactive_context, ("lock",))
    assert isinstance(numeric, GuidedCommand)
    assert isinstance(text, GuidedCommand)
    assert numeric == text
    assert "研究" in capsys.readouterr().out


def test_research_publish_is_dangerous(interactive_context, monkeypatch) -> None:
    answers = iter(["plan.json", "evidence.json"])
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(answers))
    command = research.handle(interactive_context, ("publish",))
    assert isinstance(command, GuidedCommand)
    assert command.dangerous is True
