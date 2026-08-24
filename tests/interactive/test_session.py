from __future__ import annotations

from kairospy.surface.cli.interactive.models import GuidedCommand, ShellControl
from kairospy.surface.cli.interactive.session import (
    _prompt_label,
    _run_shell,
    prompt_path,
    shell_command,
)


def test_prompt_label_adds_a_product_breadcrumb(interactive_context) -> None:
    interactive_context.shell_path = ("operations", "market")

    assert _prompt_label(interactive_context) == "首页 / 系统与配置 / 市场行情"
    assert prompt_path(interactive_context) == "/operations/market"


def test_root_dispatches_to_product_section(interactive_context) -> None:
    assert shell_command(interactive_context, "risk") is ShellControl.HANDLED
    assert interactive_context.shell_path == ("risk",)
    assert prompt_path(interactive_context) == "/risk"


def test_root_numeric_navigation_uses_product_groups(interactive_context) -> None:
    assert shell_command(interactive_context, "6") is ShellControl.HANDLED
    assert interactive_context.shell_path == ("operations",)
    assert shell_command(interactive_context, "4") is ShellControl.HANDLED
    assert interactive_context.shell_path == ("config",)


def test_top_level_strategy_navigation_exposes_launch_and_observation(
    interactive_context,
) -> None:
    assert shell_command(interactive_context, "3") is ShellControl.HANDLED
    assert interactive_context.shell_path == ("strategy",)
    assert shell_command(interactive_context, "1") is ShellControl.HANDLED
    assert interactive_context.shell_path == ("launch",)


def test_section_dispatch_returns_guided_command(interactive_context) -> None:
    interactive_context.shell_path = ("risk",)
    command = shell_command(interactive_context, "doctor")
    assert isinstance(command, GuidedCommand)
    assert command.argv == ("risk", "doctor")


def test_invalid_shell_quoting_is_handled(interactive_context, capsys) -> None:
    assert shell_command(interactive_context, "'unfinished") is None
    assert "命令解析失败" in capsys.readouterr().out


def test_question_mark_opens_global_help(
    interactive_context, monkeypatch, capsys
) -> None:
    lines = iter(["?", "exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(lines))

    assert (
        _run_shell(
            interactive_context,
            execute=lambda _argv: 0,
            yes=False,
        )
        == 0
    )
    assert "输入 1-6 选择产品入口" in capsys.readouterr().out


def test_ctrl_c_at_shell_prompt_cancels_only_current_input(
    interactive_context, monkeypatch, capsys
) -> None:
    calls = iter((KeyboardInterrupt(), "exit"))

    def read(_prompt=""):
        value = next(calls)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr("builtins.input", read)

    assert _run_shell(interactive_context, execute=lambda _argv: 0, yes=False) == 0
    assert "已取消当前输入" in capsys.readouterr().out


def test_top_level_market_enters_standalone_scope(interactive_context) -> None:
    assert shell_command(interactive_context, "market") is ShellControl.HANDLED
    assert interactive_context.shell_path == ("market",)
    assert prompt_path(interactive_context) == "/market"


def test_cancelled_provider_selection_does_not_execute_a_command(
    interactive_context, monkeypatch
) -> None:
    from types import SimpleNamespace

    from kairospy.surface.cli.interactive.sections.business import market

    interactive_context.shell_path = ("system", "market")
    record = SimpleNamespace(id="market:binance:spot:BTCUSDT")
    monkeypatch.setattr(market.reference, "select_market", lambda _context: record)
    monkeypatch.setattr(
        market,
        "_load_routes",
        lambda *_args: {
            "routes": [
                {
                    "provider": "binance",
                    "state": "ready",
                },
                {
                    "provider": "massive",
                    "state": "ready",
                },
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
