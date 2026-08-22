from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand, ShellControl
from kairospy.surface.cli.interactive.session import _run_shell, prompt_path, shell_command


def test_root_dispatches_to_product_section(interactive_context) -> None:
    assert shell_command(interactive_context, "risk") is ShellControl.HANDLED
    assert interactive_context.shell_path == ("risk",)
    assert prompt_path(interactive_context) == "/risk"


def test_root_numeric_navigation_uses_product_groups(interactive_context) -> None:
    assert shell_command(interactive_context, "5") is ShellControl.HANDLED
    assert interactive_context.shell_path == ("operations",)
    assert shell_command(interactive_context, "4") is ShellControl.HANDLED
    assert interactive_context.shell_path == ("config",)


def test_section_dispatch_returns_guided_command(interactive_context) -> None:
    interactive_context.shell_path = ("risk",)
    command = shell_command(interactive_context, "doctor")
    assert isinstance(command, GuidedCommand)
    assert command.argv == ("risk", "doctor")


def test_invalid_shell_quoting_is_handled(interactive_context, capsys) -> None:
    assert shell_command(interactive_context, "'unfinished") is None
    assert "命令解析失败" in capsys.readouterr().out


def test_top_level_market_enters_standalone_scope(interactive_context) -> None:
    assert shell_command(interactive_context, "market") is ShellControl.HANDLED
    assert interactive_context.shell_path == ("market",)
    assert prompt_path(interactive_context) == "/market"


def test_cancelled_source_selection_does_not_execute_a_command(
    interactive_context, monkeypatch
) -> None:
    from types import SimpleNamespace

    from kairospy.surface.cli.interactive.sections.business import market

    interactive_context.shell_path = ("system", "market")
    record = SimpleNamespace(id="market:binance:spot:BTCUSDT")
    monkeypatch.setattr(market.reference, "select_market", lambda _context: record)
    monkeypatch.setattr(
        market,
        "_load_sources",
        lambda *_args: {
            "sources": [
                {
                    "source_id": "binance-spot",
                    "configured": True,
                    "ready": True,
                }
            ]
        },
    )
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "b")
    lines = iter(["quote", "exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(lines))
    executed = []

    assert (
        _run_shell(
            interactive_context,
            execute=lambda argv: executed.append(tuple(argv)) or 0,
            yes=False,
        )
        == 0
    )
    assert executed == []
