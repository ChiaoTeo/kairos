from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand, ShellControl
from kairospy.surface.cli.interactive.sections.business import notifications


def test_notifications_validate_aliases(
    interactive_context, monkeypatch, capsys
) -> None:
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "paper")
    notifications.print_menu(interactive_context)
    notifications.print_help(interactive_context)
    numeric = notifications.handle(interactive_context, ("5",))
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


def test_resource_notification_center_lists_status_and_opens_detail(
    interactive_context, monkeypatch, capsys
) -> None:
    interactive_context.shell_path = ("resources", "notifications")
    monkeypatch.setattr(
        notifications,
        "_destinations",
        lambda _context: [
            {
                "destination_id": "telegram-ops",
                "provider": "telegram",
                "enabled": True,
                "credential_id": "telegram-ops",
                "verification_status": "verified",
                "last_tested_at": "2026-08-24T00:00:00Z",
                "last_test_detail": "delivery accepted",
            }
        ],
    )
    monkeypatch.setattr(notifications, "_references", lambda *_args: [])

    notifications.print_menu(interactive_context)
    assert "telegram-ops · telegram · 已验证" in capsys.readouterr().out
    assert notifications.handle(interactive_context, ("1",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == (
        "resources",
        "notifications",
        "telegram-ops",
    )
    notifications.print_menu(interactive_context)
    detail = capsys.readouterr().out
    assert "最近测试：2026-08-24T00:00:00Z" in detail
    assert "Launch 引用：无" in detail


def test_notification_setup_and_management_actions(
    interactive_context, monkeypatch
) -> None:
    prompts = iter(("destination", "launch", "signals", "destination", "destination"))
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(prompts))

    feishu = notifications.handle(interactive_context, ("feishu",))
    telegram = notifications.handle(interactive_context, ("telegram",))
    listing = notifications.handle(interactive_context, ("list",))
    attach = notifications.handle(interactive_context, ("attach",))
    disable = notifications.handle(interactive_context, ("disable",))
    delete = notifications.handle(interactive_context, ("delete",))

    assert feishu.argv[-1] == "feishu"
    assert telegram.argv[-1] == "telegram"
    assert listing.argv[:2] == ("notifications", "list")
    assert attach.dangerous is True
    assert disable.dangerous is True
    assert delete.dangerous is True
