from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand
from kairospy.surface.cli.interactive.sections.business import notifications


def test_notifications_validate_aliases(
    interactive_context, monkeypatch, capsys
) -> None:
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "paper")
    notifications.print_menu(interactive_context)
    notifications.print_help(interactive_context)
    numeric = notifications.handle(interactive_context, ("1",))
    text = notifications.handle(interactive_context, ("validate",))
    assert isinstance(numeric, GuidedCommand)
    assert isinstance(text, GuidedCommand)
    assert numeric.argv == text.argv
    assert "通知" in capsys.readouterr().out


def test_notification_test_is_dangerous(interactive_context, monkeypatch) -> None:
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "ops")
    command = notifications.handle(interactive_context, ("test",))
    assert isinstance(command, GuidedCommand)
    assert command.dangerous is True
