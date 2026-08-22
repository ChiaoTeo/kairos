from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand
from kairospy.surface.cli.interactive.sections.system import config


def test_config_menu_help_and_show_aliases(interactive_context, capsys) -> None:
    config.print_menu(interactive_context)
    config.print_help(interactive_context)
    numeric = config.handle(interactive_context, ("3",))
    text = config.handle(interactive_context, ("show",))
    assert isinstance(numeric, GuidedCommand)
    assert isinstance(text, GuidedCommand)
    assert numeric == text
    assert "高级配置" in capsys.readouterr().out


def test_profile_use_is_dangerous(interactive_context, monkeypatch) -> None:
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "paper")
    command = config.handle(interactive_context, ("use",))
    assert isinstance(command, GuidedCommand)
    assert command.dangerous is True
