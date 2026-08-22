from __future__ import annotations

from types import SimpleNamespace

from kairospy.surface.cli.interactive.models import GuidedCommand, ShellControl
from kairospy.surface.cli.interactive.sections.business import reference


def test_reference_numeric_and_text_navigation(
    interactive_context, capsys
) -> None:
    interactive_context.shell_path = ("reference",)
    reference.print_menu(interactive_context)
    reference.print_help(interactive_context)
    assert reference.handle(interactive_context, ("1",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("reference", "assets")
    assert "Reference 市场目录" in capsys.readouterr().out

    interactive_context.shell_path = ("reference",)
    assert reference.handle(interactive_context, ("assets",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("reference", "assets")


def test_reference_option_chain_builder(monkeypatch) -> None:
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "instrument:equity:US:AAPL:common")
    command = reference.choose_option_chain()
    assert isinstance(command, GuidedCommand)
    assert command.argv[:2] == ("reference", "option-chain")


def test_market_selection_accepts_search_and_numeric_choice_but_no_raw_id(
    interactive_context, monkeypatch
) -> None:
    record = SimpleNamespace(
        id="market:binance:spot:BTCUSDT",
        venue_symbol="BTCUSDT",
        exchange_id="exchange:binance",
        instrument_kind="spot",
        base_asset="asset:BTC",
        quote_asset="asset:USDT",
        status="active",
        instrument=SimpleNamespace(display_symbol="BTC/USDT"),
    )

    class Application:
        def find_markets(self, **filters):
            assert filters == {"query": "BTC", "active_only": True, "limit": 25}
            return (record,)

    monkeypatch.setattr(reference, "_application", lambda _context: Application())
    prompts: list[str] = []
    answers = iter(["BTC", "1"])

    def prompt(label, **_kwargs):
        prompts.append(label)
        return next(answers)

    monkeypatch.setattr("typer.prompt", prompt)

    assert reference.select_market(interactive_context) is record
    assert interactive_context.selected_market is record
    assert prompts == [
        "搜索市场代码或名称（直接回车浏览前 10 条）",
        "选择市场序号；输入 b 返回",
    ]
    assert all("market id" not in label.lower() for label in prompts)
