from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand
from kairospy.surface.cli.interactive.sections.business import account


def test_account_selected_context_builds_owned_command(
    interactive_context, capsys
) -> None:
    interactive_context.shell_path = ("account", "paper-main")
    interactive_context.selected_account = "paper-main"
    account.print_menu(interactive_context)
    account.print_help(interactive_context)
    command = account.handle(interactive_context, ("2",))
    assert isinstance(command, GuidedCommand)
    assert command.argv == (
        "account", "assets", "paper-main", "--format", "table"
    )
    assert "当前账户" in capsys.readouterr().out


def test_account_text_alias_matches_numeric(interactive_context) -> None:
    interactive_context.shell_path = ("account", "paper-main")
    interactive_context.selected_account = "paper-main"
    numeric = account.handle(interactive_context, ("3",))
    text = account.handle(interactive_context, ("positions",))
    assert isinstance(numeric, GuidedCommand)
    assert isinstance(text, GuidedCommand)
    assert numeric.argv == text.argv
