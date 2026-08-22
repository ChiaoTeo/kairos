from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand
from kairospy.surface.cli.interactive.sections.research_data import data


def test_data_menu_help_and_list_aliases(interactive_context, capsys) -> None:
    data.print_menu(interactive_context)
    data.print_help(interactive_context)
    numeric = data.handle(interactive_context, ("1",))
    text = data.handle(interactive_context, ("list",))
    assert isinstance(numeric, GuidedCommand)
    assert isinstance(text, GuidedCommand)
    assert numeric == text
    assert numeric.argv == ("data", "list")
    assert "数据" in capsys.readouterr().out


def test_data_execute_is_dangerous(interactive_context, monkeypatch) -> None:
    answers = iter(["requirements.json", "hash"])
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(answers))
    command = data.handle(interactive_context, ("execute",))
    assert isinstance(command, GuidedCommand)
    assert command.dangerous is True
